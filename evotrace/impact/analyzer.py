"""Static and empirical impact analyzer for evolving collections and queries."""

from __future__ import annotations
import copy
import logging
from typing import Any, Dict, List, Optional, Set, Tuple
from pymongo.database import Database

from evotrace.config import Config
from evotrace.database import get_db
from evotrace.evolution.paths import is_ancestor_path, is_descendant_path, paths_overlap
from evotrace.impact.registry import list_registered_queries

logger = logging.getLogger(__name__)


def evaluate_static_query_impact(
    query_paths: List[str],
    operation_type: str,
    source_path: Optional[str] = None,
    destination_path: Optional[str] = None,
) -> Tuple[str, str, str]:
    """Determine the static impact classification and rule explanation for a registered query.

    Returns:
        (classification, severity, explanation)
        classification: 'POTENTIALLY_AFFECTED' | 'LIKELY_UNAFFECTED' | 'MANUAL_REVIEW'
        severity: 'HIGH' | 'MEDIUM' | 'LOW' | 'NONE'
    """
    reasons: List[str] = []
    highest_severity = "NONE"

    target_paths = []
    if source_path:
        target_paths.append(("source", source_path))
    if destination_path:
        target_paths.append(("destination", destination_path))

    for role, target in target_paths:
        for qpath in query_paths:
            # 1. Exact match
            if qpath == target:
                if operation_type in ("delete_field", "rename_field", "move_field") and role == "source":
                    highest_severity = "HIGH"
                    reasons.append(
                        f"Query references exact path '{qpath}', which is removed/relocated by {operation_type}."
                    )
                elif operation_type == "convert_type":
                    highest_severity = "HIGH"
                    reasons.append(
                        f"Query filters on '{qpath}', which underwent safe type conversion; type changes may affect range/equality evaluations."
                    )
                elif role == "destination":
                    if highest_severity != "HIGH":
                        highest_severity = "MEDIUM"
                    reasons.append(
                        f"Query references destination path '{qpath}', into which new data was introduced."
                    )
            # 2. Target is an ancestor of the queried path
            elif is_ancestor_path(target, qpath):
                if role == "source" and operation_type in ("delete_field", "rename_field", "move_field"):
                    highest_severity = "HIGH"
                    reasons.append(
                        f"Parent structure '{target}' was modified/removed, disrupting nested queried field '{qpath}'."
                    )
                else:
                    if highest_severity not in ("HIGH",):
                        highest_severity = "MEDIUM"
                    reasons.append(
                        f"Ancestor object '{target}' was modified, which may restructure sub-object containing '{qpath}'."
                    )
            # 3. Target is a descendant of the queried path
            elif is_descendant_path(target, qpath):
                if highest_severity not in ("HIGH", "MEDIUM"):
                    highest_severity = "MEDIUM"
                reasons.append(
                    f"Child path '{target}' was altered within composite object '{qpath}' referenced by query."
                )

    if highest_severity == "HIGH":
        return (
            "POTENTIALLY_AFFECTED",
            "HIGH",
            "; ".join(reasons) or "High likelihood of query predicate disruption.",
        )
    elif highest_severity == "MEDIUM":
        return (
            "POTENTIALLY_AFFECTED",
            "MEDIUM",
            "; ".join(reasons) or "Moderate structural dependency detected.",
        )
    else:
        return (
            "LIKELY_UNAFFECTED",
            "NONE",
            f"No referenced query fields ({', '.join(query_paths)}) intersect with transformed paths.",
        )


def analyze_impact(
    collection_name: str,
    operation_type: str,
    source_path: Optional[str] = None,
    destination_path: Optional[str] = None,
    pre_transformed_docs: Optional[List[Dict[str, Any]]] = None,
    post_transformed_docs: Optional[List[Dict[str, Any]]] = None,
    db: Optional[Database] = None,
) -> Dict[str, Any]:
    """Perform comprehensive impact analysis for an evolution operation against registered queries.

    Includes both static dependency rules and empirical query execution measurements.
    """
    db = get_db() if db is None else db
    queries = list_registered_queries(collection_name=collection_name, enabled_only=True, db=db)

    query_results = []
    affected_queries_count = 0

    # For empirical checking, use provided docs or query MongoDB collection
    real_col = db[collection_name]

    for q in queries:
        q_id = q["query_id"]
        q_name = q["name"]
        q_paths = q.get("referenced_paths", [])
        filter_spec = q.get("filter_spec", {})

        classification, severity, explanation = evaluate_static_query_impact(
            query_paths=q_paths,
            operation_type=operation_type,
            source_path=source_path,
            destination_path=destination_path,
        )

        if classification == "POTENTIALLY_AFFECTED":
            affected_queries_count += 1

        # Empirical measurement
        count_before: Optional[int] = None
        count_after: Optional[int] = None
        empirical_diff: Optional[int] = None
        empirical_note: str = ""

        try:
            if pre_transformed_docs is not None and post_transformed_docs is not None:
                # Preview mode in memory: use temporary in-memory collections or mock
                import mongomock
                temp_client = mongomock.MongoClient()
                temp_db = temp_client["temp_eval"]
                pre_col = temp_db["pre"]
                post_col = temp_db["post"]
                if pre_transformed_docs:
                    pre_col.insert_many(pre_transformed_docs)
                if post_transformed_docs:
                    post_col.insert_many(post_transformed_docs)
                count_before = pre_col.count_documents(filter_spec)
                count_after = post_col.count_documents(filter_spec)
            else:
                # Live database evaluation
                count_before = q.get("last_result_count")
                count_after = real_col.count_documents(filter_spec) if hasattr(real_col, "count_documents") else len(list(real_col.find(filter_spec)))

            if count_before is not None and count_after is not None:
                empirical_diff = count_after - count_before
                if empirical_diff != 0:
                    empirical_note = (
                        f"Observed result count changed from {count_before} to {count_after} (delta: {empirical_diff:+d}). "
                        "Evidence of altered query selection behavior."
                    )
                else:
                    empirical_note = (
                        f"Observed result count remained unchanged ({count_after} docs). "
                        "Note: Unchanged count does not prove semantic equivalence."
                    )
        except Exception as eval_exc:
            empirical_note = f"Empirical evaluation unavailable: {eval_exc}"

        query_results.append({
            "query_id": q_id,
            "name": q_name,
            "description": q.get("description", ""),
            "referenced_paths": q_paths,
            "filter_spec": filter_spec,
            "classification": classification,
            "severity": severity,
            "explanation": explanation,
            "count_before": count_before,
            "count_after": count_after,
            "empirical_diff": empirical_diff,
            "empirical_note": empirical_note,
        })

    return {
        "collection_name": collection_name,
        "operation_type": operation_type,
        "source_path": source_path,
        "destination_path": destination_path,
        "total_registered_queries": len(queries),
        "affected_queries_count": affected_queries_count,
        "query_impacts": query_results,
    }
