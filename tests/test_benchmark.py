"""Unit tests for synthetic dataset generator, metrics computation, and benchmark runner."""

from evotrace.evaluation.generator import generate_benchmark_dataset
from evotrace.evaluation.benchmark import (
    compute_classification_metrics,
    compute_document_impact_metrics,
    compute_query_impact_accuracy,
    compute_recovery_correctness,
    documents_are_deep_equal,
    run_single_evaluation,
    run_benchmark_suite,
    get_all_experiment_results,
)
from evotrace.evaluation.export import (
    export_experiments_to_csv,
    export_experiments_to_json,
)
from evotrace.evolution.engine import execute_evolution
from evotrace.recovery.engine import execute_recovery


def test_synthetic_dataset_generator():
    docs, gt = generate_benchmark_dataset(size=50, seed=42)
    assert len(docs) == 50
    assert gt["dataset_size"] == 50
    assert gt["discount_code_docs_count"] > 0
    assert gt["shipping_city_docs_count"] > 0
    assert len(gt["discount_code_doc_ids"]) == gt["discount_code_docs_count"]
    assert len(gt["shipping_city_doc_ids"]) == gt["shipping_city_docs_count"]
    assert len(gt["all_doc_ids"]) == 50

    # Test seed determinism
    docs2, gt2 = generate_benchmark_dataset(size=50, seed=42)
    assert docs[0]["account_id"] == docs2[0]["account_id"]
    assert docs[0].get("discount_code") == docs2[0].get("discount_code")
    assert gt["discount_code_doc_ids"] == gt2["discount_code_doc_ids"]


def test_metric_computations():
    # Perfect detection
    prec, rec, f1 = compute_classification_metrics(tp=10, fp=0, fn=0)
    assert prec == 1.0 and rec == 1.0 and f1 == 1.0

    # Partial precision/recall
    prec, rec, f1 = compute_classification_metrics(tp=8, fp=2, fn=2)
    assert prec == 0.8
    assert rec == 0.8
    assert f1 == 0.8

    # Edge cases (zeros)
    prec, rec, f1 = compute_classification_metrics(tp=0, fp=0, fn=0)
    assert prec == 1.0 and rec == 1.0 and f1 == 1.0


def test_set_based_precision_recall_fp_fn_handling():
    """Verify production document impact metrics handle false positives, false negatives, duplicates, and empty sets."""
    expected_ids = ["doc_1", "doc_2", "doc_3", "doc_4"]

    # Scenario A: False positive (actual has extra doc_5)
    actual_with_fp = ["doc_1", "doc_2", "doc_3", "doc_4", "doc_5"]
    res_a = compute_document_impact_metrics(actual_with_fp, expected_ids)
    assert res_a["precision"] == 0.8  # 4 / 5
    assert res_a["recall"] == 1.0     # 4 / 4
    assert res_a["f1_score"] < 1.0
    assert res_a["fp"] == 1
    assert res_a["fn"] == 0

    # Scenario B: False negative (actual missed doc_4)
    actual_with_fn = ["doc_1", "doc_2", "doc_3"]
    res_b = compute_document_impact_metrics(actual_with_fn, expected_ids)
    assert res_b["precision"] == 1.0   # 3 / 3
    assert res_b["recall"] == 0.75    # 3 / 4
    assert res_b["f1_score"] < 1.0
    assert res_b["fp"] == 0
    assert res_b["fn"] == 1

    # Scenario C: Duplicates in predictions should be deduplicated
    actual_with_dups = ["doc_1", "doc_1", "doc_2", "doc_3", "doc_4"]
    res_c = compute_document_impact_metrics(actual_with_dups, expected_ids)
    assert res_c["precision"] == 1.0
    assert res_c["recall"] == 1.0
    assert res_c["f1_score"] == 1.0

    # Scenario D: Missing all predictions when expected has items (no silent perfect scores)
    res_d = compute_document_impact_metrics([], expected_ids)
    assert res_d["recall"] == 0.0
    assert res_d["f1_score"] == 0.0
    assert res_d["fn"] == 4

    # Scenario E: Pure false positives when none were expected (no silent perfect score)
    res_e = compute_document_impact_metrics(["extra_1", "extra_2"], [])
    assert res_e["precision"] == 0.0
    assert res_e["f1_score"] == 0.0
    assert res_e["fp"] == 2


def test_documents_are_deep_equal_missing_vs_null_and_nested():
    """Verify deep document equality detects missing vs explicit nulls, types, and nested dicts."""
    # 1. Missing vs explicit null must be unequal
    doc_missing = {"name": "Alice"}
    doc_null = {"name": "Alice", "notes": None}
    assert documents_are_deep_equal(doc_missing, doc_null) is False

    # 2. Both explicit nulls must be equal
    doc_null2 = {"name": "Alice", "notes": None}
    assert documents_are_deep_equal(doc_null, doc_null2) is True

    # 3. Type divergence (integer vs boolean) must be unequal
    doc_int = {"status": 1}
    doc_bool = {"status": True}
    assert documents_are_deep_equal(doc_int, doc_bool) is False

    # 4. Nested dictionary mismatch
    doc_nested_1 = {"user": {"address": {"city": "Boston", "zip": "02138"}}}
    doc_nested_2 = {"user": {"address": {"city": "Cambridge", "zip": "02138"}}}
    assert documents_are_deep_equal(doc_nested_1, doc_nested_2) is False

    # 5. Nested dictionary match
    doc_nested_3 = {"user": {"address": {"city": "Boston", "zip": "02138"}}}
    assert documents_are_deep_equal(doc_nested_1, doc_nested_3) is True


def test_recovery_correctness_detects_deliberate_corruption(clean_database):
    """Regression test: deliberately corrupt a restored field and verify production recovery correctness detects it."""
    import copy
    col = clean_database["eval_corruption_test"]
    docs, gt = generate_benchmark_dataset(size=10, seed=99)
    col.insert_many(copy.deepcopy(docs))
    pre_docs_map = {str(d["_id"]): copy.deepcopy(d) for d in docs}

    # Execute rename_field
    op_dict, prov_dict = execute_evolution(
        collection_name="eval_corruption_test",
        operation_type="rename_field",
        source_path="discount_code",
        destination_path="promo_code",
        db=clean_database,
    )

    # Execute recovery
    rec_res = execute_recovery(operation_id=op_dict["operation_id"], db=clean_database)
    restored_ids = rec_res["restored_doc_ids"]
    assert len(restored_ids) > 0

    # 1. Initially, evaluate using production compute_recovery_correctness
    current_docs_map = {did: col.find_one({"_id": did}) for did in restored_ids}
    res_clean = compute_recovery_correctness(
        restored_docs_map=current_docs_map,
        pre_evolution_docs_map=pre_docs_map,
        restored_doc_ids=restored_ids,
        conflict_doc_ids=rec_res.get("skipped_doc_ids", []),
    )
    assert res_clean["recovery_correctness"] == 1.0
    assert res_clean["correct_count"] == len(restored_ids)
    assert res_clean["mismatched_count"] == 0

    # 2. Deliberately corrupt one restored document in the database
    corrupted_id = restored_ids[0]
    col.update_one({"_id": corrupted_id}, {"$set": {"discount_code": "CORRUPTED_VALUE_XYZ"}})

    # Check recovery verification again using production function
    current_docs_map_corrupted = {did: col.find_one({"_id": did}) for did in restored_ids}
    res_corrupted = compute_recovery_correctness(
        restored_docs_map=current_docs_map_corrupted,
        pre_evolution_docs_map=pre_docs_map,
        restored_doc_ids=restored_ids,
        conflict_doc_ids=rec_res.get("skipped_doc_ids", []),
    )
    # Must detect the corruption, decrement correct_count, and drop below 1.0!
    assert res_corrupted["correct_count"] == len(restored_ids) - 1
    assert res_corrupted["mismatched_count"] == 1
    assert res_corrupted["mismatches"][0]["doc_id"] == corrupted_id
    assert res_corrupted["recovery_correctness"] < 1.0


def test_query_classification_accuracy_mismatch_detection():
    """Verify that production query impact accuracy detects mismatches, extra queries, and missing queries."""
    expected_labels = {
        "q1": "POTENTIALLY_AFFECTED",
        "q2": "LIKELY_UNAFFECTED",
        "q3": "POTENTIALLY_AFFECTED",
    }

    # Perfect match
    predictions_perfect = [
        {"query_id": "q1", "classification": "POTENTIALLY_AFFECTED"},
        {"query_id": "q2", "classification": "LIKELY_UNAFFECTED"},
        {"query_id": "q3", "classification": "POTENTIALLY_AFFECTED"},
    ]
    res_perf = compute_query_impact_accuracy(predictions_perfect, expected_labels)
    assert res_perf["accuracy"] == 1.0
    assert res_perf["correct_count"] == 3
    assert len(res_perf["mismatches"]) == 0

    # One mismatch (q2 predicted as POTENTIALLY_AFFECTED instead of LIKELY_UNAFFECTED)
    predictions_mismatch = [
        {"query_id": "q1", "classification": "POTENTIALLY_AFFECTED"},
        {"query_id": "q2", "classification": "POTENTIALLY_AFFECTED"},  # Mismatch!
        {"query_id": "q3", "classification": "POTENTIALLY_AFFECTED"},
    ]
    res_mismatch = compute_query_impact_accuracy(predictions_mismatch, expected_labels)
    assert res_mismatch["correct_count"] == 2
    assert res_mismatch["accuracy"] == round(2 / 3, 4)
    assert len(res_mismatch["mismatches"]) == 1
    assert res_mismatch["mismatches"][0]["query_id"] == "q2"

    # Missing prediction for q3: cannot silently produce perfect score
    predictions_missing = [
        {"query_id": "q1", "classification": "POTENTIALLY_AFFECTED"},
        {"query_id": "q2", "classification": "LIKELY_UNAFFECTED"},
    ]
    res_missing = compute_query_impact_accuracy(predictions_missing, expected_labels)
    assert res_missing["correct_count"] == 2
    assert res_missing["accuracy"] == round(2 / 3, 4)
    assert res_missing["mismatches"][0]["query_id"] == "q3"


def test_benchmark_runner_and_export(clean_database):
    res = run_single_evaluation(
        scenario_name="rename_field",
        dataset_size=20,
        seed=123,
        db=clean_database,
    )

    assert res["scenario_name"] == "rename_field"
    assert res["dataset_size"] == 20
    assert res["metrics"]["f1_score"] == 1.0
    assert res["metrics"]["query_impact_accuracy"] == 1.0
    assert res["metrics"]["recovery_correctness"] == 1.0
    assert res["metrics"]["recovery_restored_count"] > 0
    assert res["metrics"]["recovery_conflict_count"] == 0

    # Check persistence in database
    all_runs = get_all_experiment_results(db=clean_database)
    assert len(all_runs) >= 1

    # Check CSV and JSON export
    csv_str = export_experiments_to_csv(db=clean_database)
    assert "experiment_id,scenario_name" in csv_str
    assert "rename_field" in csv_str

    json_str = export_experiments_to_json(db=clean_database)
    assert "rename_field" in json_str
