"""Query registry for NoSQL-EvoTrace.

Allows application developers to declare named query filters with explicit field-path
dependencies. Rejects executable Python/JavaScript and enforces safe, declarative JSON filters.
"""

from __future__ import annotations
import json
import logging
import time
from typing import Any, Dict, List, Optional, Set
from pymongo.database import Database

from evotrace.config import Config
from evotrace.database import get_db, save_mock_state
from evotrace.models import RegisteredQuery

logger = logging.getLogger(__name__)

# Permitted MongoDB query operators (declarative only)
ALLOWED_QUERY_OPERATORS = {
    "$eq", "$ne", "$gt", "$gte", "$lt", "$lte",
    "$in", "$nin", "$exists", "$type", "$all",
    "$and", "$or", "$nor", "$not", "$regex"
}

DISALLOWED_OPERATORS = {
    "$where", "$accumulator", "$function", "$expr"
}


class QueryValidationError(Exception):
    """Raised when a registered query specification is invalid or contains forbidden operators."""
    pass


def extract_paths_from_filter(filter_spec: Dict[str, Any], prefix: str = "") -> Set[str]:
    """Recursively discover all field paths referenced in a MongoDB query filter."""
    paths: Set[str] = set()

    for key, value in filter_spec.items():
        if key in ("$and", "$or", "$nor"):
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        paths.update(extract_paths_from_filter(item, prefix))
        elif key.startswith("$"):
            # Operator inside a field value
            continue
        else:
            current_path = f"{prefix}.{key}" if prefix else str(key)
            paths.add(current_path)
            if isinstance(value, dict):
                # Check for subdocuments or nested operators
                has_operators = any(k.startswith("$") for k in value.keys())
                if not has_operators:
                    paths.update(extract_paths_from_filter(value, current_path))

    return paths


def validate_filter_spec(filter_spec: Any) -> None:
    """Ensure filter spec is a valid, safe dictionary containing only permitted operators."""
    if not isinstance(filter_spec, dict):
        raise QueryValidationError("Query filter specification must be a JSON dictionary.")

    def _inspect(obj: Any):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in DISALLOWED_OPERATORS:
                    raise QueryValidationError(
                        f"Forbidden operator '{k}' detected. Arbitrary script execution is disallowed."
                    )
                if k.startswith("$") and k not in ALLOWED_QUERY_OPERATORS:
                    raise QueryValidationError(
                        f"Operator '{k}' is not in the allowed declarative operator whitelist."
                    )
                _inspect(v)
        elif isinstance(obj, list):
            for item in obj:
                _inspect(item)

    _inspect(filter_spec)


def register_query(
    name: str,
    collection_name: str,
    description: str,
    filter_spec: Dict[str, Any],
    referenced_paths: Optional[List[str]] = None,
    enabled: bool = True,
    db: Optional[Database] = None,
) -> Dict[str, Any]:
    """Register a new validated query filter in the catalog."""
    if not name or not name.strip():
        raise QueryValidationError("Query name cannot be empty.")
    if not collection_name or not collection_name.strip():
        raise QueryValidationError("Collection name cannot be empty.")

    validate_filter_spec(filter_spec)

    auto_discovered = sorted(list(extract_paths_from_filter(filter_spec)))
    if referenced_paths:
        # Merge declared with discovered paths
        all_paths = sorted(list(set(referenced_paths) | set(auto_discovered)))
    else:
        all_paths = auto_discovered

    query_model = RegisteredQuery.create_new(
        name=name.strip(),
        collection_name=collection_name.strip(),
        description=description.strip(),
        filter_spec=filter_spec,
        referenced_paths=all_paths,
        enabled=enabled,
    )

    db = get_db() if db is None else db
    col = db[Config.COLLECTION_QUERIES]
    col.insert_one(query_model.to_dict())
    save_mock_state()

    logger.info("Registered application query '%s' for collection '%s'.", query_model.name, collection_name)
    return query_model.to_dict()


def list_registered_queries(
    collection_name: Optional[str] = None,
    enabled_only: bool = False,
    db: Optional[Database] = None,
) -> List[Dict[str, Any]]:
    """Retrieve registered queries matching filter parameters."""
    db = get_db() if db is None else db
    col = db[Config.COLLECTION_QUERIES]

    q: Dict[str, Any] = {}
    if collection_name:
        q["collection_name"] = collection_name
    if enabled_only:
        q["enabled"] = True

    cursor = col.find(q).sort("created_at", -1)
    results = []
    for doc in cursor:
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        results.append(doc)
    return results


def get_query_by_id(query_id: str, db: Optional[Database] = None) -> Optional[Dict[str, Any]]:
    """Fetch single registered query by its ID."""
    db = get_db() if db is None else db
    col = db[Config.COLLECTION_QUERIES]
    doc = col.find_one({"query_id": query_id})
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


def update_query_status(query_id: str, enabled: bool, db: Optional[Database] = None) -> bool:
    """Enable or disable a registered query."""
    db = get_db() if db is None else db
    col = db[Config.COLLECTION_QUERIES]
    res = col.update_one({"query_id": query_id}, {"$set": {"enabled": enabled}})
    return res.modified_count > 0


def delete_registered_query(query_id: str, db: Optional[Database] = None) -> bool:
    """Remove a registered query from the catalog."""
    db = get_db() if db is None else db
    col = db[Config.COLLECTION_QUERIES]
    res = col.delete_one({"query_id": query_id})
    return res.deleted_count > 0


def execute_registered_query(query_id: str, db: Optional[Database] = None) -> Dict[str, Any]:
    """Execute a registered query against the collection and record outcome."""
    db = get_db() if db is None else db
    q_doc = get_query_by_id(query_id, db=db)
    if not q_doc:
        raise ValueError(f"Query {query_id} not found.")

    col = db[q_doc["collection_name"]]
    start_time = time.perf_counter()
    matching_docs = list(col.find(q_doc["filter_spec"]).limit(100))
    count = col.count_documents(q_doc["filter_spec"]) if hasattr(col, "count_documents") else len(list(col.find(q_doc["filter_spec"])))
    elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

    # Update query statistics
    queries_col = db[Config.COLLECTION_QUERIES]
    queries_col.update_one(
        {"query_id": query_id},
        {"$set": {
            "last_run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "last_result_count": count,
        }}
    )

    sample_ids = [str(d.get("_id")) for d in matching_docs[:5]]

    return {
        "query_id": query_id,
        "name": q_doc["name"],
        "collection_name": q_doc["collection_name"],
        "count": count,
        "execution_time_ms": elapsed_ms,
        "sample_ids": sample_ids,
    }
