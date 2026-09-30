"""Provenance explorer and query engine with configurable privacy redaction."""

from __future__ import annotations
import copy
import logging
from typing import Any, Dict, List, Optional
from pymongo.database import Database

from evotrace.config import Config
from evotrace.database import get_db

logger = logging.getLogger(__name__)


def redact_field_change(change: Dict[str, Any]) -> Dict[str, Any]:
    """Create a privacy-redacted copy of a document field change."""
    sanitized = copy.deepcopy(change)
    if "pre_state" in sanitized and sanitized["pre_state"].get("exists"):
        sanitized["pre_state"]["value"] = "***REDACTED***"
    if "post_state" in sanitized and sanitized["post_state"].get("exists"):
        sanitized["post_state"]["value"] = "***REDACTED***"
    return sanitized


def list_provenance_records(
    collection_name: Optional[str] = None,
    operation_type: Optional[str] = None,
    doc_id: Optional[str] = None,
    include_recovery: bool = True,
    redact_values: Optional[bool] = None,
    limit: int = 50,
    db: Optional[Database] = None,
) -> List[Dict[str, Any]]:
    """List and filter historical provenance records."""
    db = get_db() if db is None else db
    col = db[Config.COLLECTION_PROVENANCE]

    query: Dict[str, Any] = {}
    if collection_name:
        query["collection_name"] = collection_name
    if operation_type:
        query["operation_type"] = operation_type
    if not include_recovery:
        query["is_recovery"] = False
    if doc_id:
        query["affected_doc_ids"] = str(doc_id)

    cursor = col.find(query).sort("timestamp", -1).limit(limit)
    results = []

    should_redact = Config.REDACT_SENSITIVE_PROVENANCE if redact_values is None else redact_values

    for doc in cursor:
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])

        if should_redact and "field_changes" in doc:
            doc["field_changes"] = [redact_field_change(fc) for fc in doc["field_changes"]]

        results.append(doc)

    return results


def get_operation_provenance_details(
    operation_id: str,
    redact_values: Optional[bool] = None,
    db: Optional[Database] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch complete provenance details for an operation including recovery status and schema links."""
    db = get_db() if db is None else db
    col = db[Config.COLLECTION_PROVENANCE]
    doc = col.find_one({"operation_id": operation_id, "is_recovery": False})
    if not doc:
        return None

    if "_id" in doc:
        doc["_id"] = str(doc["_id"])

    # Check if a recovery event has been logged for this operation
    recovery_event = col.find_one({"recovers_operation_id": operation_id, "is_recovery": True})
    doc["is_recovered"] = recovery_event is not None
    doc["recovery_event"] = {
        "provenance_id": recovery_event["provenance_id"],
        "timestamp": recovery_event["timestamp"],
        "metadata": recovery_event.get("metadata", {}),
    } if recovery_event else None

    # Retrieve associated operation record
    ops_col = db[Config.COLLECTION_OPERATIONS]
    op_doc = ops_col.find_one({"operation_id": operation_id})
    if op_doc and "_id" in op_doc:
        op_doc["_id"] = str(op_doc["_id"])
    doc["operation_details"] = op_doc

    should_redact = Config.REDACT_SENSITIVE_PROVENANCE if redact_values is None else redact_values
    if should_redact and "field_changes" in doc:
        doc["field_changes"] = [redact_field_change(fc) for fc in doc["field_changes"]]

    return doc
