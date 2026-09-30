"""Unit tests for fine-grained provenance recording, redaction, and storage overhead."""

from evotrace.evolution.operations import compute_value_fingerprint
from evotrace.evolution.engine import execute_evolution
from evotrace.provenance.recorder import (
    calculate_provenance_storage_bytes,
    get_provenance_by_operation_id,
)
from evotrace.provenance.explorer import redact_field_change, list_provenance_records


def test_value_fingerprints():
    fp_absent = compute_value_fingerprint(None, False)
    assert fp_absent == "FINGERPRINT_ABSENT"

    fp_null = compute_value_fingerprint(None, True)
    assert fp_null == "FINGERPRINT_NULL"

    fp_val1 = compute_value_fingerprint("hello", True)
    fp_val2 = compute_value_fingerprint("hello", True)
    fp_diff = compute_value_fingerprint("world", True)

    assert fp_val1 == fp_val2
    assert fp_val1 != fp_diff


def test_provenance_preserves_absent_vs_null(clean_database):
    col = clean_database["users"]
    col.insert_many([
        {"_id": "u1", "note": None},      # Explicit null
        {"_id": "u2"},                    # Absent note
        {"_id": "u3", "note": "VIP user"} # Value
    ])

    # Delete note field
    op, prov = execute_evolution(
        collection_name="users",
        operation_type="delete_field",
        source_path="note",
        db=clean_database,
    )

    changes = {c["doc_id"]: c for c in prov["field_changes"]}

    # u1 had explicit null: existed=True, is_null=True
    assert changes["u1"]["pre_state"]["exists"] is True
    assert changes["u1"]["pre_state"]["is_null"] is True
    assert changes["u1"]["pre_state"]["value"] is None

    # u3 had value: existed=True, is_null=False
    assert changes["u3"]["pre_state"]["exists"] is True
    assert changes["u3"]["pre_state"]["is_null"] is False
    assert changes["u3"]["pre_state"]["value"] == "VIP user"

    # u2 did not have note, so it was skipped by delete_field
    assert "u2" not in changes


def test_provenance_redaction():
    change = {
        "doc_id": "u1",
        "pre_state": {"exists": True, "value": "secret_password_123"},
        "post_state": {"exists": True, "value": "hashed_pw"},
    }
    redacted = redact_field_change(change)
    assert redacted["pre_state"]["value"] == "***REDACTED***"
    assert redacted["post_state"]["value"] == "***REDACTED***"
    # Original should be untouched
    assert change["pre_state"]["value"] == "secret_password_123"


def test_calculate_provenance_bytes():
    sample_data = {
        "operation_id": "op_test123",
        "field_changes": [
            {"doc_id": "1", "pre_state": {"value": "old"}, "post_state": {"value": "new"}},
        ],
    }
    b = calculate_provenance_storage_bytes(sample_data)
    assert b > 0
    assert isinstance(b, int)
