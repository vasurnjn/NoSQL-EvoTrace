"""Unit tests for query registry, static path intersection, and empirical impact analysis."""

import pytest
from evotrace.impact.registry import (
    register_query,
    list_registered_queries,
    validate_filter_spec,
    extract_paths_from_filter,
    execute_registered_query,
    QueryValidationError,
)
from evotrace.impact.analyzer import evaluate_static_query_impact, analyze_impact


def test_query_filter_validation():
    # Valid filter
    valid_filter = {"status": "active", "address.city": {"$eq": "Boston"}, "age": {"$gte": 21}}
    validate_filter_spec(valid_filter)

    # Disallowed $where operator
    with pytest.raises(QueryValidationError):
        validate_filter_spec({"$where": "this.name == 'admin'"})

    # Non-dictionary filter
    with pytest.raises(QueryValidationError):
        validate_filter_spec(["status", "active"])


def test_extract_paths_from_filter():
    filter_spec = {
        "status": "active",
        "$or": [
            {"loyalty_points": {"$gte": 500}},
            {"address": {"city": "Cambridge"}},
        ],
    }
    paths = extract_paths_from_filter(filter_spec)
    assert "status" in paths
    assert "loyalty_points" in paths
    assert "address.city" in paths or "address" in paths


def test_static_query_impact_rules():
    query_paths = ["address.city", "status"]

    # 1. Exact match with delete_field -> HIGH severity
    cls, sev, exp = evaluate_static_query_impact(query_paths, "delete_field", source_path="address.city")
    assert cls == "POTENTIALLY_AFFECTED"
    assert sev == "HIGH"
    assert "Direct modification" in exp or "exact path" in exp

    # 2. Ancestor modification (address deleted/moved) -> HIGH severity
    cls, sev, exp = evaluate_static_query_impact(query_paths, "delete_field", source_path="address")
    assert cls == "POTENTIALLY_AFFECTED"
    assert sev == "HIGH"
    assert "Parent structure" in exp

    # 3. Disjoint path (e.g. tags or postal_code mutated) -> LIKELY_UNAFFECTED
    cls, sev, exp = evaluate_static_query_impact(query_paths, "delete_field", source_path="postal_code")
    assert cls == "LIKELY_UNAFFECTED"
    assert sev == "NONE"


def test_empirical_impact_analysis(clean_database):
    col = clean_database["demo_inventory"]
    col.insert_many([
        {"_id": "p1", "sku": "SKU-1", "warehouse": "East"},
        {"_id": "p2", "sku": "SKU-2", "warehouse": "West"},
        {"_id": "p3", "sku": "SKU-3", "warehouse": "East"},
    ])

    q = register_query(
        name="East Warehouse Items",
        collection_name="demo_inventory",
        description="Filter east warehouse",
        filter_spec={"warehouse": "East"},
        referenced_paths=["warehouse"],
        db=clean_database,
    )

    # Analyze impact of renaming warehouse -> facility
    impact = analyze_impact(
        collection_name="demo_inventory",
        operation_type="rename_field",
        source_path="warehouse",
        destination_path="facility",
        db=clean_database,
    )

    assert impact["total_registered_queries"] == 1
    assert impact["affected_queries_count"] == 1
    q_eval = impact["query_impacts"][0]
    assert q_eval["classification"] == "POTENTIALLY_AFFECTED"
    assert q_eval["severity"] == "HIGH"


def test_execute_registered_query_function(clean_database):
    col = clean_database["demo_inventory"]
    col.insert_many([
        {"_id": "p1", "warehouse": "East"},
        {"_id": "p2", "warehouse": "West"},
    ])
    q = register_query(
        name="East Warehouse Test",
        collection_name="demo_inventory",
        description="test",
        filter_spec={"warehouse": "East"},
        db=clean_database,
    )
    outcome = execute_registered_query(q["query_id"], db=clean_database)
    assert outcome["count"] == 1
    assert outcome["execution_time_ms"] >= 0

