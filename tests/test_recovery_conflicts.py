"""Unit tests for recovery inversion, downstream conflict detection, and idempotency."""

import pytest
from evotrace.evolution.engine import execute_evolution
from evotrace.recovery.engine import preview_recovery, execute_recovery


def test_recovery_add_field(clean_database):
    col = clean_database["profiles"]
    col.insert_many([
        {"_id": "p1", "username": "alice"},
        {"_id": "p2", "username": "bob"},
    ])

    # 1. Add field
    op, prov = execute_evolution(
        collection_name="profiles",
        operation_type="add_field",
        destination_path="is_verified",
        default_value=True,
        db=clean_database,
    )

    # 2. Preview recovery
    prev = preview_recovery(op["operation_id"], db=clean_database)
    assert prev["restorable_count"] == 2
    assert prev["conflict_count"] == 0
    assert prev["can_recover_cleanly"] is True

    # 3. Execute recovery
    rec = execute_recovery(op["operation_id"], db=clean_database)
    assert rec["restored_count"] == 2

    # Verify documents restored (is_verified removed)
    doc1 = col.find_one({"_id": "p1"})
    assert "is_verified" not in doc1


def test_recovery_delete_field_preserves_missing_vs_null(clean_database):
    col = clean_database["accounts"]
    col.insert_many([
        {"_id": "a1", "notes": "VIP"},
        {"_id": "a2", "notes": None}, # Explicit null
    ])

    # Delete notes
    op, prov = execute_evolution(
        collection_name="accounts",
        operation_type="delete_field",
        source_path="notes",
        db=clean_database,
    )

    # Verify deleted in DB
    assert "notes" not in col.find_one({"_id": "a1"})

    # Recover
    rec = execute_recovery(op["operation_id"], db=clean_database)
    assert rec["restored_count"] == 2

    # Check restored states
    res_a1 = col.find_one({"_id": "a1"})
    res_a2 = col.find_one({"_id": "a2"})

    assert res_a1["notes"] == "VIP"
    assert res_a2["notes"] is None and "notes" in res_a2


def test_recovery_conflict_detection(clean_database):
    col = clean_database["items"]
    col.insert_many([
        {"_id": "i1", "cost": 10},
        {"_id": "i2", "cost": 20},
    ])

    # Rename cost -> price
    op, prov = execute_evolution(
        collection_name="items",
        operation_type="rename_field",
        source_path="cost",
        destination_path="price",
        db=clean_database,
    )

    # Simulate downstream user edit on document i2: user changed price to 999!
    col.update_one({"_id": "i2"}, {"$set": {"price": 999}})

    # Preview recovery: should flag conflict on i2
    prev = preview_recovery(op["operation_id"], db=clean_database)
    assert prev["restorable_count"] == 1
    assert prev["conflict_count"] == 1
    assert prev["conflicts"][0]["doc_id"] == "i2"

    # Execute recovery with safe skip
    rec = execute_recovery(op["operation_id"], skip_conflicting_docs=True, db=clean_database)
    assert rec["restored_count"] == 1
    assert rec["conflict_count"] == 1

    # i1 should be recovered (price -> cost: 10)
    res_i1 = col.find_one({"_id": "i1"})
    assert "cost" in res_i1 and "price" not in res_i1

    # i2 should NOT be overwritten (price: 999 preserved!)
    res_i2 = col.find_one({"_id": "i2"})
    assert res_i2["price"] == 999


def test_recovery_idempotency_duplicate_prevention(clean_database):
    col = clean_database["records"]
    col.insert_one({"_id": "r1", "tag": "v1"})

    op, _ = execute_evolution(
        collection_name="records",
        operation_type="rename_field",
        source_path="tag",
        destination_path="version",
        db=clean_database,
    )

    # First recovery
    execute_recovery(op["operation_id"], db=clean_database)

    # Second recovery attempt should be rejected as duplicate
    with pytest.raises(ValueError, match="already been recovered"):
        execute_recovery(op["operation_id"], db=clean_database)


def test_recovery_nested_paths(clean_database):
    """Verify recovery correctly restores flat-to-nested and nested-to-flat moves."""
    col = clean_database["users"]
    col.insert_many([
        {"_id": "u1", "city": "Seattle", "name": "Alice"},
        {"_id": "u2", "city": "Portland", "name": "Bob"},
    ])

    # Move city -> address.city (flat to nested)
    op, _ = execute_evolution(
        collection_name="users",
        operation_type="move_field",
        source_path="city",
        destination_path="address.city",
        db=clean_database,
    )

    doc_after = col.find_one({"_id": "u1"})
    assert "city" not in doc_after
    assert doc_after["address"]["city"] == "Seattle"

    # Recover
    rec = execute_recovery(op["operation_id"], db=clean_database)
    assert rec["restored_count"] == 2
    assert rec["conflict_count"] == 0

    doc_restored = col.find_one({"_id": "u1"})
    assert doc_restored["city"] == "Seattle"
    assert "address" not in doc_restored or doc_restored.get("address") == {}


def test_recovery_deleted_document_conflict(clean_database):
    """Verify recovery detects deleted documents, skips them, and reports conflict reason."""
    col = clean_database["orders"]
    col.insert_many([
        {"_id": "o1", "status": "pending"},
        {"_id": "o2", "status": "pending"},
    ])

    op, _ = execute_evolution(
        collection_name="orders",
        operation_type="rename_field",
        source_path="status",
        destination_path="order_status",
        db=clean_database,
    )

    # Downstream deletion of document o2
    col.delete_one({"_id": "o2"})

    # Preview recovery
    prev = preview_recovery(op["operation_id"], db=clean_database)
    assert prev["restorable_count"] == 1
    assert prev["conflict_count"] == 1
    assert prev["conflicts"][0]["doc_id"] == "o2"
    assert "deleted downstream" in prev["conflicts"][0]["reason"]

    # Execute recovery with safe skip
    rec = execute_recovery(op["operation_id"], skip_conflicting_docs=True, db=clean_database)
    assert rec["restored_count"] == 1
    assert rec["conflict_count"] == 1
    assert "o1" in rec["restored_doc_ids"]
    assert "o2" in rec["skipped_doc_ids"]

    # o1 is restored to status
    assert col.find_one({"_id": "o1"})["status"] == "pending"


def test_recovery_conflicting_source_field_on_rename(clean_database):
    """Verify recovery detects when source path was re-created downstream and prevents overwrite."""
    col = clean_database["products"]
    col.insert_one({"_id": "prod1", "cost": 50, "title": "Desk"})

    # Rename cost -> price
    op, _ = execute_evolution(
        collection_name="products",
        operation_type="rename_field",
        source_path="cost",
        destination_path="price",
        db=clean_database,
    )

    # Downstream edit re-creates 'cost' with a new meaning or value
    col.update_one({"_id": "prod1"}, {"$set": {"cost": 42}})

    # Preview recovery: should detect source conflict
    prev = preview_recovery(op["operation_id"], db=clean_database)
    assert prev["restorable_count"] == 0
    assert prev["conflict_count"] == 1
    assert "already exists in current document" in prev["conflicts"][0]["reason"]

    # Execute recovery should skip and preserve current document
    rec = execute_recovery(op["operation_id"], skip_conflicting_docs=True, db=clean_database)
    assert rec["restored_count"] == 0
    assert rec["conflict_count"] == 1

    doc = col.find_one({"_id": "prod1"})
    assert doc["cost"] == 42  # Downstream source data preserved
    assert doc["price"] == 50  # Destination untouched


def test_recovery_preserves_unrelated_downstream_edits(clean_database):
    """Verify recovery restores the targeted field while preserving unrelated modifications made to other fields."""
    col = clean_database["customers"]
    col.insert_one({"_id": "c1", "tier": "silver", "profile": {"level": 1, "note": "initial"}})

    # Evolution: rename tier -> customer_tier
    op, _ = execute_evolution(
        collection_name="customers",
        operation_type="rename_field",
        source_path="tier",
        destination_path="customer_tier",
        db=clean_database,
    )

    # Downstream edits on unrelated fields: update profile.level to 2, add new field "tags"
    col.update_one({"_id": "c1"}, {"$set": {
        "profile.level": 2,
        "profile.note": "updated downstream",
        "tags": ["vip", "early-adopter"]
    }})

    # Execute recovery
    rec = execute_recovery(op["operation_id"], db=clean_database)
    assert rec["restored_count"] == 1
    assert rec["conflict_count"] == 0

    restored = col.find_one({"_id": "c1"})
    # Reverted field
    assert restored["tier"] == "silver"
    assert "customer_tier" not in restored
    # Unrelated fields preserved intact!
    assert restored["profile"]["level"] == 2
    assert restored["profile"]["note"] == "updated downstream"
    assert restored["tags"] == ["vip", "early-adopter"]


def test_recovery_append_only_audit_records(clean_database):
    """Verify recovery creates an append-only provenance record with is_recovery=True and preserves original."""
    from evotrace.config import Config
    from evotrace.provenance.recorder import get_provenance_by_operation_id

    col = clean_database["audit_test"]
    col.insert_one({"_id": "x1", "flag": False})

    op, orig_prov = execute_evolution(
        collection_name="audit_test",
        operation_type="rename_field",
        source_path="flag",
        destination_path="active",
        db=clean_database,
    )

    rec = execute_recovery(op["operation_id"], db=clean_database)

    prov_col = clean_database[Config.COLLECTION_PROVENANCE]
    all_provs = list(prov_col.find({"collection_name": "audit_test"}))
    assert len(all_provs) == 2

    # Original record is preserved
    orig_record = get_provenance_by_operation_id(op["operation_id"], db=clean_database)
    assert orig_record is not None
    assert orig_record["is_recovery"] is False

    # Recovery record is marked
    rec_record = prov_col.find_one({"provenance_id": rec["recovery_provenance_id"]})
    assert rec_record is not None
    assert rec_record["is_recovery"] is True
    assert rec_record["recovers_operation_id"] == op["operation_id"]


def test_recovery_convert_type_with_arrays_and_primitives(clean_database):
    """Verify recovery for convert_type restores original string type and preserves array fields intact."""
    from evotrace.evaluation.benchmark import documents_are_deep_equal
    import copy

    col = clean_database["type_recovery_test"]
    original_doc = {
        "_id": "t1",
        "score": "88",
        "audit_trail": [1, 2, 3],
        "metadata": {"source": "survey", "tags": ["alpha", "beta"]},
    }
    col.insert_one(copy.deepcopy(original_doc))

    # Convert score from string to integer
    op, prov = execute_evolution(
        collection_name="type_recovery_test",
        operation_type="convert_type",
        source_path="score",
        target_type="integer",
        db=clean_database,
    )

    doc_evolved = col.find_one({"_id": "t1"})
    assert doc_evolved["score"] == 88
    assert isinstance(doc_evolved["score"], int)

    # Recover
    rec = execute_recovery(op["operation_id"], db=clean_database)
    assert rec["restored_count"] == 1
    assert rec["conflict_count"] == 0

    doc_recovered = col.find_one({"_id": "t1"})
    assert doc_recovered["score"] == "88"
    assert isinstance(doc_recovered["score"], str)
    assert documents_are_deep_equal(doc_recovered, original_doc) is True


def test_preview_recovery_already_recovered_reports_zero_conflicts(clean_database):
    """Regression test: verify that preview_recovery on an already-recovered operation
    reports Already Recovered, 0 restorable, 0 conflicts, and does not alter documents or audit records."""
    from evotrace.config import Config
    import copy

    col = clean_database["already_recovered_test"]
    initial_docs = [
        {"_id": f"user_{i}", "name": f"User {i}", "postal_code": f"0213{i}"}
        for i in range(10)
    ]
    col.insert_many(copy.deepcopy(initial_docs))

    # 1. Execute evolution (rename postal_code -> zip_code)
    op, prov = execute_evolution(
        collection_name="already_recovered_test",
        operation_type="rename_field",
        source_path="postal_code",
        destination_path="zip_code",
        db=clean_database,
    )
    op_id = op["operation_id"]
    assert len(prov["affected_doc_ids"]) == 10

    # 2. Successfully recover the operation
    rec = execute_recovery(op_id, db=clean_database)
    assert rec["restored_count"] == 10
    assert rec["conflict_count"] == 0

    # Snapshot documents and audit counts before calling preview_recovery
    docs_before_preview = list(col.find({}))
    prov_col = clean_database[Config.COLLECTION_PROVENANCE]
    prov_count_before = prov_col.count_documents({})
    ops_col = clean_database[Config.COLLECTION_OPERATIONS]
    ops_count_before = ops_col.count_documents({})

    # 3. Call preview_recovery again on the already-recovered operation
    prev = preview_recovery(op_id, db=clean_database)

    # 4. Verify preview output
    assert prev["is_already_recovered"] is True
    assert prev["status"] == "Already Recovered"
    assert prev["total_documents_in_provenance"] == 10
    assert prev["restorable_count"] == 0
    assert prev["conflict_count"] == 0
    assert prev["skipped_count"] == 0
    assert prev["can_recover_cleanly"] is False
    assert prev["conflicts"] == []
    assert "already been recovered" in prev["explanation"].lower()

    # 5. Verify no documents or audit records were modified by preview
    docs_after_preview = list(col.find({}))
    assert docs_after_preview == docs_before_preview
    assert prov_col.count_documents({}) == prov_count_before
    assert ops_col.count_documents({}) == ops_count_before
