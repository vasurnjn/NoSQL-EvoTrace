"""Schema inference engine for MongoDB collections.

Inspects actual stored documents, discovers observed field paths (including nested),
records type distributions, presence frequencies, and null frequencies, and computes
deterministic schema fingerprints.
"""

from __future__ import annotations
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Set, Tuple
from pymongo.database import Database

from evotrace.config import Config
from evotrace.database import get_db
from evotrace.models import FieldStats, SchemaSnapshot

logger = logging.getLogger(__name__)


def map_bson_python_type(val: Any) -> str:
    """Return a normalized string representation of a field value's type."""
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "boolean"
    if isinstance(val, int):
        return "integer"
    if isinstance(val, float):
        return "double"
    if isinstance(val, str):
        return "string"
    if isinstance(val, list):
        return "array"
    if isinstance(val, dict):
        return "object"
    # Fallback to class name (e.g. ObjectId, datetime)
    return type(val).__name__.lower()


def extract_document_paths(
    doc: Dict[str, Any],
    prefix: str = "",
) -> List[Tuple[str, Any, str]]:
    """Recursively extract all (path, value, type_name) tuples from a document.

    Nested objects are traversed with dot notation (e.g. 'address.city').
    Arrays are represented as path 'tags[]' or inspected if containing dicts.
    """
    results: List[Tuple[str, Any, str]] = []

    for key, value in doc.items():
        # Skip internal MongoDB _id if needed, but in schema inference we record it as objectId
        current_path = f"{prefix}.{key}" if prefix else str(key)
        val_type = map_bson_python_type(value)

        results.append((current_path, value, val_type))

        if isinstance(value, dict) and value:
            results.extend(extract_document_paths(value, prefix=current_path))
        elif isinstance(value, list) and value:
            # Check if list contains dicts to expose nested structure e.g. items[].product_id
            for idx, item in enumerate(value):
                if isinstance(item, dict):
                    item_path = f"{current_path}[]"
                    results.append((item_path, item, "object"))
                    results.extend(extract_document_paths(item, prefix=item_path))
                    break  # Sample first element for array-of-objects structure

    return results


def compute_schema_fingerprint(fields_dict: Dict[str, Dict[str, Any]]) -> str:
    """Compute a deterministic SHA-256 fingerprint from inferred field profiles."""
    canonical_items = []
    for path in sorted(fields_dict.keys()):
        field_info = fields_dict[path]
        types_sorted = sorted(field_info.get("types", {}).keys())
        presence_ratio = round(field_info.get("presence_ratio", 0.0), 4)
        canonical_items.append({
            "path": path,
            "types": types_sorted,
            "presence_ratio": presence_ratio,
        })

    serialized = json.dumps(canonical_items, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def infer_schema(
    collection_name: str,
    db: Optional[Database] = None,
    save_snapshot: bool = True,
    operation_id: Optional[str] = None,
    description: str = "",
) -> SchemaSnapshot:
    """Infer the observed schema profile from all documents in the collection."""
    db = get_db() if db is None else db
    collection = db[collection_name]

    documents = list(collection.find({}))
    total_docs = len(documents)

    if total_docs == 0:
        empty_snapshot = SchemaSnapshot.create_new(
            collection_name=collection_name,
            fingerprint="empty_00000000",
            document_count=0,
            fields_data={},
            operation_id=operation_id,
            description=description or "Inferred empty collection schema",
        )
        if save_snapshot:
            snapshots_col = db[Config.COLLECTION_SNAPSHOTS]
            snapshots_col.insert_one(empty_snapshot.to_dict())
        return empty_snapshot

    # Aggregators for field path statistics
    path_occurrences: Dict[str, int] = {}
    path_nulls: Dict[str, int] = {}
    path_type_counts: Dict[str, Dict[str, int]] = {}
    path_samples: Dict[str, List[str]] = {}

    for doc in documents:
        extracted = extract_document_paths(doc)
        seen_paths_in_doc: Set[str] = set()

        for path, val, val_type in extracted:
            if path not in path_occurrences:
                path_occurrences[path] = 0
                path_nulls[path] = 0
                path_type_counts[path] = {}
                path_samples[path] = []

            # Track presence once per document
            if path not in seen_paths_in_doc:
                path_occurrences[path] += 1
                seen_paths_in_doc.add(path)

            if val is None:
                path_nulls[path] += 1

            # Count type observation
            path_type_counts[path][val_type] = path_type_counts[path].get(val_type, 0) + 1

            # Collect sample values (limit to 3 distinct representation strings)
            if len(path_samples[path]) < 3:
                s_val = str(val)
                if len(s_val) > 40:
                    s_val = s_val[:37] + "..."
                if s_val not in path_samples[path]:
                    path_samples[path].append(s_val)

    # Build final FieldStats mapping
    fields_data: Dict[str, Dict[str, Any]] = {}
    for path, presence_cnt in sorted(path_occurrences.items()):
        type_counts = path_type_counts[path]
        non_null_types = {t: c for t, c in type_counts.items() if t != "null"}
        is_mixed = len(non_null_types) > 1

        stats = FieldStats(
            path=path,
            types=type_counts,
            presence_count=presence_cnt,
            null_count=path_nulls[path],
            missing_count=total_docs - presence_cnt,
            presence_ratio=round(presence_cnt / total_docs, 4),
            is_mixed_type=is_mixed,
            samples=path_samples[path],
        )
        fields_data[path] = stats.to_dict()

    fingerprint = compute_schema_fingerprint(fields_data)

    snapshot = SchemaSnapshot.create_new(
        collection_name=collection_name,
        fingerprint=fingerprint,
        document_count=total_docs,
        fields_data=fields_data,
        operation_id=operation_id,
        description=description or f"Inferred from {total_docs} documents",
    )

    if save_snapshot:
        snapshots_col = db[Config.COLLECTION_SNAPSHOTS]
        snapshots_col.insert_one(snapshot.to_dict())
        logger.info(
            "Saved schema snapshot %s for collection %s (fingerprint=%s)",
            snapshot.snapshot_id,
            collection_name,
            fingerprint,
        )

    return snapshot


def get_latest_snapshot(collection_name: str, db: Optional[Database] = None) -> Optional[Dict[str, Any]]:
    """Retrieve the most recent schema snapshot for a collection."""
    db = get_db() if db is None else db
    snapshots_col = db[Config.COLLECTION_SNAPSHOTS]
    doc = snapshots_col.find_one(
        {"collection_name": collection_name},
        sort=[("created_at", -1)],
    )
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


def get_snapshot_history(collection_name: str, db: Optional[Database] = None) -> List[Dict[str, Any]]:
    """Retrieve all recorded schema snapshots for a collection in reverse chronological order."""
    db = get_db() if db is None else db
    snapshots_col = db[Config.COLLECTION_SNAPSHOTS]
    cursor = snapshots_col.find({"collection_name": collection_name}).sort("created_at", -1)
    results = []
    for doc in cursor:
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        results.append(doc)
    return results


def get_snapshot_by_id(snapshot_id: str, db: Optional[Database] = None) -> Optional[Dict[str, Any]]:
    """Retrieve a specific schema snapshot by its unique ID."""
    db = get_db() if db is None else db
    snapshots_col = db[Config.COLLECTION_SNAPSHOTS]
    doc = snapshots_col.find_one({"snapshot_id": snapshot_id})
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc
