"""Unit tests for schema inference, mixed-type detection, fingerprints, and comparator."""

from evotrace.schema.inference import (
    infer_schema,
    compute_schema_fingerprint,
    extract_document_paths,
    map_bson_python_type,
)
from evotrace.schema.comparator import compare_snapshots


def test_map_bson_python_type():
    assert map_bson_python_type(None) == "null"
    assert map_bson_python_type(True) == "boolean"
    assert map_bson_python_type(42) == "integer"
    assert map_bson_python_type(3.14) == "double"
    assert map_bson_python_type("hello") == "string"
    assert map_bson_python_type(["a", "b"]) == "array"
    assert map_bson_python_type({"k": "v"}) == "object"


def test_schema_inference_empty_collection(clean_database):
    snap = infer_schema("empty_col", db=clean_database, save_snapshot=True)
    assert snap.document_count == 0
    assert snap.schema_fingerprint == "empty_00000000"
    assert len(snap.fields) == 0


def test_schema_inference_heterogeneous_docs(clean_database):
    col = clean_database["customers"]
    col.insert_many([
        {
            "_id": "c1",
            "name": "Alice",
            "age": 30,
            "address": {"city": "Boston", "zip": "02138"},
            "notes": "VIP member",
            "tags": ["tech", "lead"],
        },
        {
            "_id": "c2",
            "name": "Bob",
            "age": "thirty-five",  # Mixed type with integer above!
            "address": {"city": "Seattle"},
            "notes": None,  # Explicitly null
            "tags": ["retail"],
        },
        {
            "_id": "c3",
            "name": "Clara",
            # age is missing!
            # address is missing!
            # notes is missing!
            "tags": [],
        },
    ])

    snap = infer_schema("customers", db=clean_database, save_snapshot=True)
    assert snap.document_count == 3
    assert snap.schema_fingerprint != "empty_00000000"

    # Name: 100% presence
    assert snap.fields["name"]["presence_count"] == 3
    assert snap.fields["name"]["presence_ratio"] == 1.0
    assert snap.fields["name"]["is_mixed_type"] is False

    # Age: mixed type (integer in c1, string in c2)
    assert snap.fields["age"]["is_mixed_type"] is True
    assert "integer" in snap.fields["age"]["types"]
    assert "string" in snap.fields["age"]["types"]
    assert snap.fields["age"]["missing_count"] == 1

    # Notes: present in 2 docs (1 string, 1 null), missing in 1
    assert snap.fields["notes"]["presence_count"] == 2
    assert snap.fields["notes"]["null_count"] == 1
    assert snap.fields["notes"]["missing_count"] == 1

    # Address & Address.city
    assert snap.fields["address"]["presence_count"] == 2
    assert snap.fields["address.city"]["presence_count"] == 2


def test_schema_snapshot_comparison(clean_database):
    snap_a = {
        "snapshot_id": "snap_1",
        "schema_fingerprint": "fp_111",
        "created_at": "2026-10-20T00:00:00Z",
        "document_count": 10,
        "fields": {
            "name": {"types": {"string": 10}, "presence_ratio": 1.0, "is_mixed_type": False},
            "postal_code": {"types": {"string": 10}, "presence_ratio": 1.0, "is_mixed_type": False},
        },
    }

    snap_b = {
        "snapshot_id": "snap_2",
        "schema_fingerprint": "fp_222",
        "created_at": "2026-10-20T01:00:00Z",
        "document_count": 10,
        "fields": {
            "name": {"types": {"string": 10}, "presence_ratio": 1.0, "is_mixed_type": False},
            "zip_code": {"types": {"string": 10}, "presence_ratio": 1.0, "is_mixed_type": False},  # Renamed from postal_code
            "is_verified": {"types": {"boolean": 10}, "presence_ratio": 1.0, "is_mixed_type": False},  # Added
        },
    }

    diff = compare_snapshots(snap_a, snap_b)
    assert diff["is_identical"] is False
    assert diff["stats"]["total_added"] == 2  # zip_code, is_verified
    assert diff["stats"]["total_removed"] == 1  # postal_code
    assert diff["stats"]["total_unmodified"] == 1  # name
