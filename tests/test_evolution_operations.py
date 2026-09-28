"""Unit tests for schema evolution transformations and engine execution."""

import pytest
from evotrace.evolution.operations import (
    apply_add_field,
    apply_delete_field,
    apply_rename_field,
    apply_move_field,
    apply_convert_type,
)
from evotrace.evolution.validation import (
    validate_operation_spec,
    validate_safe_type_conversion,
    EvolutionValidationError,
)
from evotrace.evolution.engine import preview_evolution, execute_evolution


def test_validation_spec_rules():
    # Valid spec
    validate_operation_spec("add_field", "my_collection", destination_path="email_verified", default_value=False)

    # Invalid operation type
    with pytest.raises(EvolutionValidationError):
        validate_operation_spec("drop_database", "my_collection")

    # Reserved system collection mutation rejected
    with pytest.raises(EvolutionValidationError):
        validate_operation_spec("add_field", "evotrace_provenance", destination_path="col")

    # Invalid path syntax or empty
    with pytest.raises(EvolutionValidationError):
        validate_operation_spec("add_field", "my_collection", destination_path="")

    # Mutating _id rejected
    with pytest.raises(EvolutionValidationError):
        validate_operation_spec("delete_field", "my_collection", source_path="_id")


def test_safe_type_conversion_rules():
    # Safe int parsing
    success, val, err = validate_safe_type_conversion("123", "integer")
    assert success is True
    assert val == 123

    # Safe float to int (exact whole number)
    success, val, err = validate_safe_type_conversion(42.0, "integer")
    assert success is True
    assert val == 42

    # Lossy float to int (rejected!)
    success, val, err = validate_safe_type_conversion(42.7, "integer")
    assert success is False
    assert "Lossy conversion" in err

    # String with non-numeric text to int (rejected!)
    success, val, err = validate_safe_type_conversion("abc", "integer")
    assert success is False

    # Boolean parsing
    success, val, err = validate_safe_type_conversion("true", "boolean")
    assert success is True and val is True
    success, val, err = validate_safe_type_conversion("false", "boolean")
    assert success is True and val is False

    # Null value preserves null
    success, val, err = validate_safe_type_conversion(None, "integer")
    assert success is True and val is None


def test_apply_add_and_delete_field():
    doc = {"_id": "1", "name": "Alice"}

    # Add field
    new_doc, change, outcome, _ = apply_add_field(doc, "tier", "silver")
    assert outcome == "applied"
    assert new_doc["tier"] == "silver"
    assert change["pre_state"]["exists"] is False
    assert change["post_state"]["exists"] is True
    assert change["post_state"]["value"] == "silver"

    # Delete field
    del_doc, change2, outcome2, _ = apply_delete_field(new_doc, "tier")
    assert outcome2 == "applied"
    assert "tier" not in del_doc
    assert change2["pre_state"]["exists"] is True
    assert change2["post_state"]["exists"] is False


def test_apply_move_field_flat_to_nested_and_reverse():
    doc = {"_id": "1", "city": "Cambridge"}

    # Move flat 'city' to nested 'address.city'
    nested_doc, change, outcome, _ = apply_move_field(doc, "city", "address.city")
    assert outcome == "applied"
    assert "city" not in nested_doc
    assert nested_doc["address"]["city"] == "Cambridge"

    # Move back nested 'address.city' to flat 'city'
    flat_doc, change2, outcome2, _ = apply_move_field(nested_doc, "address.city", "city")
    assert outcome2 == "applied"
    assert flat_doc["city"] == "Cambridge"
    # Empty address container should be cleaned
    assert "address" not in flat_doc


def test_preview_and_execute_evolution(clean_database):
    col = clean_database["orders"]
    col.insert_many([
        {"_id": "o1", "amount": "100", "status": "shipped"},
        {"_id": "o2", "amount": "250", "status": "pending"},
        {"_id": "o3", "amount": "invalid_num", "status": "cancelled"},  # Will cause conflict in type conversion
    ])

    # Dry-run preview
    preview = preview_evolution(
        collection_name="orders",
        operation_type="convert_type",
        source_path="amount",
        target_type="integer",
        db=clean_database,
    )
    assert preview["total_documents"] == 3
    assert preview["affected_count"] == 2
    assert preview["conflict_count"] == 1
    assert preview["can_execute_cleanly"] is False

    # Execute with skip_conflicts=True
    op_dict, prov_dict = execute_evolution(
        collection_name="orders",
        operation_type="convert_type",
        source_path="amount",
        target_type="integer",
        skip_conflicts=True,
        db=clean_database,
    )
    assert op_dict["status"] == "EXECUTED"
    assert len(prov_dict["affected_doc_ids"]) == 2

    # Check database state
    doc_o1 = col.find_one({"_id": "o1"})
    assert doc_o1["amount"] == 100 and isinstance(doc_o1["amount"], int)

    # Conflicting doc remains unchanged
    doc_o3 = col.find_one({"_id": "o3"})
    assert doc_o3["amount"] == "invalid_num"
