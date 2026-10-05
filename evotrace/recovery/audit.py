"""Recovery audit history and idempotency checker."""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from pymongo.database import Database

from evotrace.config import Config
from evotrace.database import get_db
from evotrace.provenance.recorder import get_recovery_audit_for_operation


def get_all_recovery_audits(
    collection_name: Optional[str] = None,
    limit: int = 50,
    db: Optional[Database] = None,
) -> List[Dict[str, Any]]:
    """Retrieve all recovery audit events in reverse chronological order."""
    db = get_db() if db is None else db
    col = db[Config.COLLECTION_PROVENANCE]

    q: Dict[str, Any] = {"is_recovery": True}
    if collection_name:
        q["collection_name"] = collection_name

    cursor = col.find(q).sort("timestamp", -1).limit(limit)
    results = []
    for doc in cursor:
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        results.append(doc)
    return results


def check_recovery_eligibility(
    operation_id: str,
    db: Optional[Database] = None,
) -> Dict[str, Any]:
    """Check whether an operation is eligible for recovery and if prior recoveries exist."""
    db = get_db() if db is None else db
    ops_col = db[Config.COLLECTION_OPERATIONS]
    op = ops_col.find_one({"operation_id": operation_id})

    if not op:
        return {"eligible": False, "reason": f"Operation {operation_id} does not exist."}

    if op.get("status") != "EXECUTED":
        return {
            "eligible": False,
            "reason": f"Cannot recover operation with status '{op.get('status')}'. Only EXECUTED operations can be recovered.",
        }

    prior = get_recovery_audit_for_operation(operation_id, db=db)
    if prior:
        return {
            "eligible": False,
            "is_already_recovered": True,
            "prior_recoveries": prior,
            "reason": f"Operation has already been recovered {len(prior)} time(s).",
        }

    return {"eligible": True, "is_already_recovered": False, "operation": op}
