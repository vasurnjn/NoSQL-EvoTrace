"""Reproducible research benchmark runner for NoSQL-EvoTrace.

Evaluates document impact precision/recall/F1, query impact accuracy, recovery correctness,
operation latency, and provenance storage overhead against known ground truths with exact ID and deep document equality.
"""

from __future__ import annotations
import copy
import logging
import platform
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple
from pymongo.database import Database

from evotrace.config import Config
from evotrace.database import get_db, get_connection_status
from evotrace.evaluation.generator import generate_benchmark_dataset
from evotrace.evolution.engine import execute_evolution
from evotrace.impact.analyzer import analyze_impact
from evotrace.impact.registry import register_query, delete_registered_query
from evotrace.models import ExperimentResult
from evotrace.provenance.recorder import calculate_provenance_storage_bytes
from evotrace.recovery.engine import execute_recovery

logger = logging.getLogger(__name__)


def documents_are_deep_equal(doc_a: Any, doc_b: Any) -> bool:
    """Compare two documents for complete structural and value equality.

    Distinguishes absent fields from explicit nulls, verifies nested objects,
    lists, and primitive types without lossy coercion.
    """
    if type(doc_a) is not type(doc_b):
        # Allow MongoDB string vs ObjectId representation equivalence
        if str(doc_a) == str(doc_b) and "ObjectId" in (type(doc_a).__name__, type(doc_b).__name__):
            return True
        return False

    if isinstance(doc_a, dict):
        if set(doc_a.keys()) != set(doc_b.keys()):
            return False
        for k in doc_a:
            if not documents_are_deep_equal(doc_a[k], doc_b[k]):
                return False
        return True

    elif isinstance(doc_a, list):
        if len(doc_a) != len(doc_b):
            return False
        for item_a, item_b in zip(doc_a, doc_b):
            if not documents_are_deep_equal(item_a, item_b):
                return False
        return True

    else:
        # Primitives: ensure boolean vs integer distinction (e.g. 1 != True)
        if isinstance(doc_a, bool) or isinstance(doc_b, bool):
            return isinstance(doc_a, bool) and isinstance(doc_b, bool) and doc_a is doc_b
        return doc_a == doc_b


def compute_classification_metrics(tp: int, fp: int, fn: int) -> Tuple[float, float, float]:
    """Calculate Precision, Recall, and F1-Score safely avoiding division by zero.

    - If no items are expected and none are predicted (tp=0, fp=0, fn=0), returns (1.0, 1.0, 1.0).
    - If items are expected but none are predicted (tp=0, fn>0), returns (0.0, 0.0, 0.0).
    - If no items are expected but some are predicted (tp=0, fp>0), returns (0.0, 0.0, 0.0).
    """
    if tp == 0 and fp == 0 and fn == 0:
        return 1.0, 1.0, 1.0
    if tp == 0:
        return 0.0, 0.0, 0.0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    return round(precision, 4), round(recall, 4), round(f1, 4)


def compute_document_impact_metrics(
    actual_doc_ids: Any,
    expected_doc_ids: Any,
) -> Dict[str, Any]:
    """Calculate exact document-impact precision, recall, and F1 using set-based ID comparisons.

    Properly handles false positives, false negatives, duplicates, and empty sets.
    Ensures missing predictions (FN) and extra predictions (FP) decrease metrics appropriately.

    Returns:
        {
            "precision": float,
            "recall": float,
            "f1_score": float,
            "tp": int,
            "fp": int,
            "fn": int,
            "actual_count": int,
            "expected_count": int,
        }
    """
    actual_set: Set[str] = {str(d) for d in (actual_doc_ids or [])}
    expected_set: Set[str] = {str(d) for d in (expected_doc_ids or [])}

    tp_set = actual_set & expected_set
    fp_set = actual_set - expected_set
    fn_set = expected_set - actual_set

    tp = len(tp_set)
    fp = len(fp_set)
    fn = len(fn_set)

    precision, recall, f1 = compute_classification_metrics(tp, fp, fn)

    return {
        "precision": precision,
        "recall": recall,
        "f1_score": f1,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "actual_count": len(actual_set),
        "expected_count": len(expected_set),
    }


def compute_query_impact_accuracy(
    predicted_query_classifications: Any,
    expected_query_classifications: Dict[str, str],
) -> Dict[str, Any]:
    """Calculate query impact classification accuracy against independently specified ground-truth labels.

    Args:
        predicted_query_classifications: Either a dict mapping query_id -> classification string,
            or a list of dicts each containing 'query_id' and 'classification'.
        expected_query_classifications: Dict mapping query_id -> expected classification string.

    Returns:
        {
            "accuracy": float,
            "total_queries": int,
            "correct_count": int,
            "mismatches": List[Dict[str, Any]],
        }
    """
    pred_map: Dict[str, str] = {}
    if isinstance(predicted_query_classifications, dict):
        pred_map = {str(k): str(v) for k, v in predicted_query_classifications.items()}
    elif isinstance(predicted_query_classifications, list):
        for item in predicted_query_classifications:
            if isinstance(item, dict) and "query_id" in item:
                pred_map[str(item["query_id"])] = str(item.get("classification", ""))

    total_expected = len(expected_query_classifications)
    if total_expected == 0:
        return {
            "accuracy": 1.0,
            "total_queries": 0,
            "correct_count": 0,
            "mismatches": [],
        }

    correct_count = 0
    mismatches: List[Dict[str, Any]] = []

    for q_id, expected_label in expected_query_classifications.items():
        q_id_str = str(q_id)
        actual_label = pred_map.get(q_id_str)
        if actual_label is not None and actual_label == expected_label:
            correct_count += 1
        else:
            mismatches.append({
                "query_id": q_id_str,
                "expected": expected_label,
                "actual": actual_label,
            })

    accuracy = round(correct_count / total_expected, 4)
    return {
        "accuracy": accuracy,
        "total_queries": total_expected,
        "correct_count": correct_count,
        "mismatches": mismatches,
    }


def compute_recovery_correctness(
    restored_docs_map: Dict[str, Dict[str, Any]],
    pre_evolution_docs_map: Dict[str, Dict[str, Any]],
    restored_doc_ids: List[str],
    conflict_doc_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Calculate recovery correctness by deep comparison against captured pre-evolution documents.

    Excludes conflicting/skipped documents from successful restorations and reports them separately.
    Returns:
        {
            "recovery_correctness": float,
            "restored_count": int,
            "correct_count": int,
            "mismatched_count": int,
            "conflict_count": int,
            "mismatches": List[Dict[str, Any]],
        }
    """
    conflict_ids = set(str(d) for d in (conflict_doc_ids or []))
    active_restored_ids = [str(d) for d in restored_doc_ids if str(d) not in conflict_ids]

    if not active_restored_ids:
        return {
            "recovery_correctness": 1.0 if len(pre_evolution_docs_map) == 0 else 0.0,
            "restored_count": 0,
            "correct_count": 0,
            "mismatched_count": 0,
            "conflict_count": len(conflict_ids),
            "mismatches": [],
        }

    correct_count = 0
    mismatches: List[Dict[str, Any]] = []

    for doc_id_str in active_restored_ids:
        orig = pre_evolution_docs_map.get(doc_id_str)
        restored = restored_docs_map.get(doc_id_str)
        if orig is not None and restored is not None and documents_are_deep_equal(restored, orig):
            correct_count += 1
        else:
            mismatches.append({"doc_id": doc_id_str})

    correctness = round(correct_count / len(active_restored_ids), 4)
    return {
        "recovery_correctness": correctness,
        "restored_count": len(active_restored_ids),
        "correct_count": correct_count,
        "mismatched_count": len(mismatches),
        "conflict_count": len(conflict_ids),
        "mismatches": mismatches,
    }


def run_single_evaluation(
    scenario_name: str,
    dataset_size: int = 100,
    seed: int = 42,
    db: Optional[Database] = None,
) -> Dict[str, Any]:
    """Execute a single controlled evaluation scenario against ground-truth data."""
    db = get_db() if db is None else db
    temp_col_name = f"eval_{scenario_name}_{uuid.uuid4().hex[:8]}"
    col = db[temp_col_name]

    # 1. Generate dataset with exact ground-truth IDs
    docs, gt = generate_benchmark_dataset(size=dataset_size, seed=seed, collection_name=temp_col_name)
    col.insert_many(copy.deepcopy(docs))

    # Pre-capture complete ground-truth document snapshots for recovery verification
    pre_docs_map: Dict[str, Dict[str, Any]] = {str(d["_id"]): copy.deepcopy(d) for d in docs}

    # 2. Configure scenario operation and scenario-specific ground truth
    if scenario_name == "rename_field":
        op_type = "rename_field"
        src_path = "discount_code"
        dst_path = "promo_code"
        expected_affected_doc_ids = gt["discount_code_doc_ids"]
        val = None
        target_t = None

        # Scenario-specific queries
        q1 = register_query(
            name=f"BenchQ_Direct_{temp_col_name}",
            collection_name=temp_col_name,
            description="Direct dependency test query",
            filter_spec={"discount_code": {"$exists": True}},
            referenced_paths=["discount_code"],
            enabled=True,
            db=db,
        )
        q2 = register_query(
            name=f"BenchQ_Disjoint_{temp_col_name}",
            collection_name=temp_col_name,
            description="Disjoint dependency test query",
            filter_spec={"usage_count": {"$gte": 100}},
            referenced_paths=["usage_count"],
            enabled=True,
            db=db,
        )
        registered_queries = [q1, q2]
        query_ground_truth = {
            q1["query_id"]: "POTENTIALLY_AFFECTED",
            q2["query_id"]: "LIKELY_UNAFFECTED",
        }

    elif scenario_name == "convert_type":
        op_type = "convert_type"
        src_path = "satisfaction_score"
        dst_path = None
        expected_affected_doc_ids = gt["satisfaction_score_doc_ids"]
        val = None
        target_t = "integer"

        q1 = register_query(
            name=f"BenchQ_Direct_{temp_col_name}",
            collection_name=temp_col_name,
            description="Direct score dependency query",
            filter_spec={"satisfaction_score": {"$gte": 50}},
            referenced_paths=["satisfaction_score"],
            enabled=True,
            db=db,
        )
        q2 = register_query(
            name=f"BenchQ_Disjoint_{temp_col_name}",
            collection_name=temp_col_name,
            description="Disjoint tier query",
            filter_spec={"tier": "gold"},
            referenced_paths=["tier"],
            enabled=True,
            db=db,
        )
        registered_queries = [q1, q2]
        query_ground_truth = {
            q1["query_id"]: "POTENTIALLY_AFFECTED",
            q2["query_id"]: "LIKELY_UNAFFECTED",
        }

    elif scenario_name == "move_field":
        op_type = "move_field"
        src_path = "shipping.city"
        dst_path = "city"
        expected_affected_doc_ids = gt["shipping_city_doc_ids"]
        val = None
        target_t = None

        q1 = register_query(
            name=f"BenchQ_Direct_{temp_col_name}",
            collection_name=temp_col_name,
            description="Direct nested shipping city query",
            filter_spec={"shipping.city": "Boston"},
            referenced_paths=["shipping.city"],
            enabled=True,
            db=db,
        )
        q2 = register_query(
            name=f"BenchQ_Ancestor_{temp_col_name}",
            collection_name=temp_col_name,
            description="Ancestor object dependency query",
            filter_spec={"shipping": {"$exists": True}},
            referenced_paths=["shipping"],
            enabled=True,
            db=db,
        )
        q3 = register_query(
            name=f"BenchQ_Disjoint_{temp_col_name}",
            collection_name=temp_col_name,
            description="Disjoint active status query",
            filter_spec={"is_active": True},
            referenced_paths=["is_active"],
            enabled=True,
            db=db,
        )
        registered_queries = [q1, q2, q3]
        query_ground_truth = {
            q1["query_id"]: "POTENTIALLY_AFFECTED",
            q2["query_id"]: "POTENTIALLY_AFFECTED",
            q3["query_id"]: "LIKELY_UNAFFECTED",
        }

    else:  # add_field default
        op_type = "add_field"
        src_path = None
        dst_path = "account_verified"
        expected_affected_doc_ids = gt["all_doc_ids"]
        val = True
        target_t = None

        q1 = register_query(
            name=f"BenchQ_Dest_{temp_col_name}",
            collection_name=temp_col_name,
            description="Destination field query",
            filter_spec={"account_verified": True},
            referenced_paths=["account_verified"],
            enabled=True,
            db=db,
        )
        q2 = register_query(
            name=f"BenchQ_Disjoint_{temp_col_name}",
            collection_name=temp_col_name,
            description="Disjoint usage query",
            filter_spec={"usage_count": {"$gte": 100}},
            referenced_paths=["usage_count"],
            enabled=True,
            db=db,
        )
        registered_queries = [q1, q2]
        query_ground_truth = {
            q1["query_id"]: "POTENTIALLY_AFFECTED",
            q2["query_id"]: "LIKELY_UNAFFECTED",
        }

    # 3. Measure Impact Analysis execution time
    t_ia_start = time.perf_counter()
    impact_result = analyze_impact(
        collection_name=temp_col_name,
        operation_type=op_type,
        source_path=src_path,
        destination_path=dst_path,
        db=db,
    )
    impact_duration_ms = round((time.perf_counter() - t_ia_start) * 1000, 2)

    # 4. Measure Evolution execution time
    t_op_start = time.perf_counter()
    op_dict, prov_dict = execute_evolution(
        collection_name=temp_col_name,
        operation_type=op_type,
        source_path=src_path,
        destination_path=dst_path,
        default_value=val,
        target_type=target_t,
        db=db,
    )
    op_duration_ms = round((time.perf_counter() - t_op_start) * 1000, 2)

    actual_affected_ids = set(prov_dict.get("affected_doc_ids", []))
    expected_ids = set(expected_affected_doc_ids)

    storage_bytes = calculate_provenance_storage_bytes(prov_dict)
    bytes_per_doc = round(storage_bytes / len(actual_affected_ids), 2) if actual_affected_ids else 0

    # 5. Evaluate Document Detection Precision/Recall/F1 by Set Comparison
    doc_metrics = compute_document_impact_metrics(
        actual_doc_ids=prov_dict.get("affected_doc_ids", []),
        expected_doc_ids=expected_affected_doc_ids,
    )
    prec = doc_metrics["precision"]
    rec = doc_metrics["recall"]
    f1 = doc_metrics["f1_score"]

    # 6. Evaluate Query Impact Classification Accuracy against Scenario-Specific Labels
    query_metrics = compute_query_impact_accuracy(
        predicted_query_classifications=impact_result.get("query_impacts", []),
        expected_query_classifications=query_ground_truth,
    )
    query_accuracy = query_metrics["accuracy"]

    # 7. Measure Recovery Correctness by Complete Document Equality
    t_rec_start = time.perf_counter()
    recovery_result = execute_recovery(
        operation_id=op_dict["operation_id"],
        db=db,
    )
    recovery_duration_ms = round((time.perf_counter() - t_rec_start) * 1000, 2)

    restored_ids = recovery_result.get("restored_doc_ids", [])
    conflict_count = recovery_result.get("conflict_count", 0)

    # Fetch restored documents from database
    restored_docs_db: Dict[str, Dict[str, Any]] = {}
    for doc_id_str in restored_ids:
        rdoc = col.find_one({"_id": doc_id_str})
        if rdoc is not None:
            restored_docs_db[str(doc_id_str)] = rdoc

    rec_metrics = compute_recovery_correctness(
        restored_docs_map=restored_docs_db,
        pre_evolution_docs_map=pre_docs_map,
        restored_doc_ids=restored_ids,
        conflict_doc_ids=recovery_result.get("skipped_doc_ids", []),
    )
    recovery_correctness = rec_metrics["recovery_correctness"]
    correct_restorations = rec_metrics["correct_count"]

    # Clean up temporary queries and collection
    for rq in registered_queries:
        delete_registered_query(rq["query_id"], db=db)
    col.drop()

    conn_info = get_connection_status()

    metrics = {
        "precision": prec,
        "recall": rec,
        "f1_score": f1,
        "query_impact_accuracy": query_accuracy,
        "recovery_correctness": recovery_correctness,
        "operation_duration_ms": op_duration_ms,
        "impact_analysis_duration_ms": impact_duration_ms,
        "recovery_duration_ms": recovery_duration_ms,
        "provenance_storage_bytes": storage_bytes,
        "bytes_per_affected_doc": bytes_per_doc,
        "processed_count": dataset_size,
        "changed_count": len(actual_affected_ids),
        "skipped_count": dataset_size - len(actual_affected_ids),
        "conflict_count": op_dict.get("summary", {}).get("conflict_count", 0),
        "recovery_restored_count": correct_restorations,
        "recovery_conflict_count": conflict_count,
        "recovery_skipped_count": conflict_count,
    }

    env_info = {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "database_backend": "Real MongoDB" if conn_info.get("is_real_mongo") else "mongomock (In-Memory)",
        "server_version": conn_info.get("server_version", "N/A"),
    }

    exp_result = ExperimentResult(
        experiment_id=f"exp_{uuid.uuid4().hex[:12]}",
        scenario_name=scenario_name,
        dataset_size=dataset_size,
        parameters={"seed": seed, "operation_type": op_type, "source_path": src_path, "destination_path": dst_path},
        metrics=metrics,
        environment_info=env_info,
    )

    # Persist experiment results into MongoDB collection
    exp_col = db[Config.COLLECTION_EXPERIMENTS]
    exp_col.insert_one(exp_result.to_dict())

    logger.info(
        "Completed benchmark '%s' (N=%d): F1=%.2f, QueryAcc=%.2f, Recovery=%.2f, OpTime=%.1fms",
        scenario_name,
        dataset_size,
        f1,
        query_accuracy,
        recovery_correctness,
        op_duration_ms,
    )

    return exp_result.to_dict()


def run_benchmark_suite(
    sizes: Optional[List[int]] = None,
    seed: int = 42,
    db: Optional[Database] = None,
) -> List[Dict[str, Any]]:
    """Execute complete reproducible research evaluation suite across multiple dataset sizes."""
    sizes = sizes or [100, 500, 1000]
    scenarios = ["rename_field", "convert_type", "move_field"]
    results = []

    for sz in sizes:
        for sc in scenarios:
            res = run_single_evaluation(scenario_name=sc, dataset_size=sz, seed=seed, db=db)
            results.append(res)

    return results


def get_all_experiment_results(db: Optional[Database] = None) -> List[Dict[str, Any]]:
    """Retrieve all persisted experiment runs sorted chronologically."""
    db = get_db() if db is None else db
    col = db[Config.COLLECTION_EXPERIMENTS]
    cursor = col.find({}).sort("timestamp", -1)
    results = []
    for doc in cursor:
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        results.append(doc)
    return results
