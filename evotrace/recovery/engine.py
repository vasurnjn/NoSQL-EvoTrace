"""Safe recovery engine for supported schema evolution operations.

Constructs inverse operations from fine-grained provenance, checks current document states
against recorded post-operation fingerprints to detect downstream edits, restores exact pre-states
(preserving absent vs explicit null), avoids overwriting conflicting source or destination values,
and logs recovery as a dedicated append-only audit event.
"""

from __future__ import annotations
import copy
import logging
from typing import Any, Dict, List, Optional, Tuple
from pymongo.database import Database
from pymongo.errors import PyMongoError

from evotrace.config import Config
from evotrace.database import get_db, get_connection_status, save_mock_state
from evotrace.evolution.operations import compute_value_fingerprint
from evotrace.evolution.paths import (
    delete_nested_path,
    get_nested_path,
    set_nested_path,
    PathStructureCollisionError,
)
from evotrace.models import ProvenanceRecord
from evotrace.provenance.recorder import (
    get_provenance_by_operation_id,
    get_recovery_audit_for_operation,
)
from evotrace.schema.inference import infer_schema

logger = logging.getLogger(__name__)


class RecoveryConflictError(Exception):
    """Raised when recovery cannot proceed due to subsequent conflicting document mutations."""
    pass


def evaluate_recovery_candidate(
    current_doc: Optional[Dict[str, Any]],
    op_type: str,
    source_path: Optional[str],
    destination_path: Optional[str],
    change: Dict[str, Any],
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Evaluate whether a document can be safely recovered without overwriting downstream edits.

    Returns:
        (can_restore, conflict_dict)
    """
    doc_id = change.get("doc_id", "unknown")

    if current_doc is None:
        return False, {
            "doc_id": doc_id,
            "reason": "Document no longer exists in collection (deleted downstream).",
        }

    post_state = change.get("post_state", {})
    expected_post_fp = post_state.get("fingerprint")

    if op_type == "add_field":
        dst = destination_path or ""
        curr_val, curr_exists, _ = get_nested_path(current_doc, dst)
        curr_fp = compute_value_fingerprint(curr_val, curr_exists)
        if curr_fp != expected_post_fp:
            return False, {
                "doc_id": doc_id,
                "reason": (
                    f"Downstream modification detected at '{dst}'. "
                    f"Current fingerprint ({curr_fp}) does not match recorded post-operation state ({expected_post_fp})."
                ),
                "current_value": str(curr_val),
                "expected_post_value": str(post_state.get("value")),
            }

    elif op_type == "delete_field":
        src = source_path or ""
        curr_val, curr_exists, _ = get_nested_path(current_doc, src)
        curr_fp = compute_value_fingerprint(curr_val, curr_exists)
        if curr_fp != "FINGERPRINT_ABSENT":
            return False, {
                "doc_id": doc_id,
                "reason": (
                    f"Downstream modification detected: source path '{src}' was recreated or populated "
                    f"downstream (current fingerprint {curr_fp}, expected absent)."
                ),
                "current_value": str(curr_val),
                "expected_post_value": None,
            }

    elif op_type in ("rename_field", "move_field"):
        src = source_path or ""
        dst = destination_path or ""

        # 1. Check destination path
        curr_dst_val, curr_dst_exists, _ = get_nested_path(current_doc, dst)
        curr_dst_fp = compute_value_fingerprint(curr_dst_val, curr_dst_exists)
        if curr_dst_fp != expected_post_fp:
            return False, {
                "doc_id": doc_id,
                "reason": (
                    f"Downstream modification detected at destination path '{dst}'. "
                    f"Current fingerprint ({curr_dst_fp}) does not match post-operation state ({expected_post_fp})."
                ),
                "current_value": str(curr_dst_val),
                "expected_post_value": str(post_state.get("value")),
            }

        # 2. Check source path (must not already exist in current document)
        curr_src_val, curr_src_exists, _ = get_nested_path(current_doc, src)
        if curr_src_exists:
            return False, {
                "doc_id": doc_id,
                "reason": (
                    f"Conflict detected: source path '{src}' already exists in current document with "
                    f"downstream data (value: {curr_src_val!r}), cannot overwrite."
                ),
                "current_value": str(curr_src_val),
                "expected_post_value": "ABSENT",
            }

    elif op_type == "convert_type":
        src = source_path or ""
        curr_val, curr_exists, _ = get_nested_path(current_doc, src)
        curr_fp = compute_value_fingerprint(curr_val, curr_exists)
        if curr_fp != expected_post_fp:
            return False, {
                "doc_id": doc_id,
                "reason": (
                    f"Downstream modification detected at '{src}'. "
                    f"Current fingerprint ({curr_fp}) does not match recorded post-operation state ({expected_post_fp})."
                ),
                "current_value": str(curr_val),
                "expected_post_value": str(post_state.get("value")),
            }

    return True, None


def apply_recovery_inversion(
    current_doc: Dict[str, Any],
    op_type: str,
    source_path: Optional[str],
    destination_path: Optional[str],
    change: Dict[str, Any],
) -> Dict[str, Any]:
    """Invert transformation on a copy of current_doc, preserving all other fields."""
    restored_doc = copy.deepcopy(current_doc)
    pre_state = change.get("pre_state", {})

    if op_type == "add_field":
        dst = destination_path or ""
        if not pre_state.get("exists", False):
            delete_nested_path(restored_doc, dst, clean_empty_parents=True)
        else:
            set_nested_path(restored_doc, dst, copy.deepcopy(pre_state.get("value")))

    elif op_type == "delete_field":
        src = source_path or ""
        if pre_state.get("exists", True):
            if pre_state.get("is_null", False):
                set_nested_path(restored_doc, src, None)
            else:
                set_nested_path(restored_doc, src, copy.deepcopy(pre_state.get("value")))

    elif op_type in ("rename_field", "move_field"):
        src = source_path or ""
        dst = destination_path or ""
        delete_nested_path(restored_doc, dst, clean_empty_parents=True)
        if pre_state.get("exists", True):
            if pre_state.get("is_null", False):
                set_nested_path(restored_doc, src, None)
            else:
                set_nested_path(restored_doc, src, copy.deepcopy(pre_state.get("value")))

    elif op_type == "convert_type":
        src = source_path or ""
        if pre_state.get("exists", True):
            if pre_state.get("is_null", False):
                set_nested_path(restored_doc, src, None)
            else:
                set_nested_path(restored_doc, src, copy.deepcopy(pre_state.get("value")))

    return restored_doc


def preview_recovery(
    operation_id: str,
    db: Optional[Database] = None,
) -> Dict[str, Any]:
    """Inspect and simulate the recovery of an operation without modifying data.

    Detects if documents have been edited since the evolution was applied.
    """
    db = get_db() if db is None else db

    # 1. Fetch original provenance
    prov = get_provenance_by_operation_id(operation_id, db=db)
    if not prov:
        raise ValueError(f"No provenance record found for operation {operation_id}.")

    # 2. Check if already recovered
    existing_recoveries = get_recovery_audit_for_operation(operation_id, db=db)
    is_already_recovered = len(existing_recoveries) > 0
    col_name = prov["collection_name"]
    field_changes = prov.get("field_changes", [])

    if is_already_recovered:
        recovery_id = existing_recoveries[0].get("provenance_id") if existing_recoveries else "N/A"
        return {
            "operation_id": operation_id,
            "collection_name": col_name,
            "operation_type": prov["operation_type"],
            "status": "Already Recovered",
            "is_already_recovered": True,
            "existing_recoveries_count": len(existing_recoveries),
            "total_documents_in_provenance": len(field_changes),
            "restorable_count": 0,
            "conflict_count": 0,
            "skipped_count": 0,
            "can_recover_cleanly": False,
            "conflicts": [],
            "sample_restorations": [],
            "explanation": "This operation has already been recovered. No further recovery is necessary.",
            "recovery_id": recovery_id,
        }

    col = db[col_name]

    restorable_count = 0
    conflict_count = 0
    conflicts: List[Dict[str, Any]] = []
    samples: List[Dict[str, Any]] = []

    op_type = prov["operation_type"]
    src_path = prov.get("source_path")
    dst_path = prov.get("destination_path")

    from bson import ObjectId

    for change in field_changes:
        doc_id_str = change["doc_id"]
        try:
            query_id = ObjectId(doc_id_str)
        except Exception:
            query_id = doc_id_str

        current_doc = col.find_one({"_id": query_id})
        if not current_doc:
            current_doc = col.find_one({"_id": doc_id_str})

        can_restore, conflict_info = evaluate_recovery_candidate(
            current_doc=current_doc,
            op_type=op_type,
            source_path=src_path,
            destination_path=dst_path,
            change=change,
        )

        if not can_restore:
            conflict_count += 1
            if conflict_info:
                conflicts.append(conflict_info)
        else:
            # Check structure collision during test application
            try:
                test_restored = apply_recovery_inversion(
                    current_doc=current_doc,
                    op_type=op_type,
                    source_path=src_path,
                    destination_path=dst_path,
                    change=change,
                )
                restorable_count += 1
                if len(samples) < 5:
                    target_p = dst_path or src_path or ""
                    samples.append({
                        "doc_id": doc_id_str,
                        "target_path": target_p,
                        "current_state": change["post_state"],
                        "state_to_restore": change["pre_state"],
                    })
            except PathStructureCollisionError as col_err:
                conflict_count += 1
                conflicts.append({
                    "doc_id": doc_id_str,
                    "reason": f"Structural collision: {col_err}",
                })

    return {
        "operation_id": operation_id,
        "collection_name": col_name,
        "operation_type": prov["operation_type"],
        "is_already_recovered": is_already_recovered,
        "existing_recoveries_count": len(existing_recoveries),
        "total_documents_in_provenance": len(field_changes),
        "restorable_count": restorable_count,
        "conflict_count": conflict_count,
        "skipped_count": conflict_count,
        "can_recover_cleanly": (conflict_count == 0 and not is_already_recovered),
        "conflicts": conflicts[:20],
        "sample_restorations": samples,
    }


def execute_recovery(
    operation_id: str,
    skip_conflicting_docs: bool = True,
    force_duplicate: bool = False,
    db: Optional[Database] = None,
) -> Dict[str, Any]:
    """Execute recovery of an operation using its fine-grained provenance record.

    Restores documents that have no downstream conflicts to their exact pre-operation state.
    Records a new append-only provenance event.
    """
    db = get_db() if db is None else db
    prov = get_provenance_by_operation_id(operation_id, db=db)
    if not prov:
        raise ValueError(f"No provenance record found for operation {operation_id}.")

    # Idempotency / Duplicate protection
    existing_recoveries = get_recovery_audit_for_operation(operation_id, db=db)
    if existing_recoveries and not force_duplicate:
        raise ValueError(
            f"Operation {operation_id} has already been recovered (Recovery ID: "
            f"{existing_recoveries[0].get('provenance_id')}). Repeated recovery rejected."
        )

    col_name = prov["collection_name"]
    col = db[col_name]
    field_changes = prov.get("field_changes", [])

    # 1. Take pre-recovery schema snapshot
    pre_recovery_snapshot = infer_schema(
        collection_name=col_name,
        db=db,
        save_snapshot=True,
        description=f"Pre-recovery snapshot for operation {operation_id}",
    )

    restored_docs: List[Tuple[Any, Dict[str, Any], Dict[str, Any]]] = []  # (doc_id, current_doc, restored_doc)
    restored_field_changes: List[Dict[str, Any]] = []
    restored_doc_ids: List[str] = []
    conflicts_encountered: List[Dict[str, Any]] = []

    op_type = prov["operation_type"]
    src_path = prov.get("source_path")
    dst_path = prov.get("destination_path")

    from bson import ObjectId

    for change in field_changes:
        doc_id_str = change["doc_id"]
        try:
            query_id = ObjectId(doc_id_str)
        except Exception:
            query_id = doc_id_str

        current_doc = col.find_one({"_id": query_id})
        if not current_doc:
            current_doc = col.find_one({"_id": doc_id_str})

        can_restore, conflict_info = evaluate_recovery_candidate(
            current_doc=current_doc,
            op_type=op_type,
            source_path=src_path,
            destination_path=dst_path,
            change=change,
        )

        if not can_restore:
            conflicts_encountered.append(conflict_info)
            if not skip_conflicting_docs:
                raise RecoveryConflictError(
                    f"Conflict detected on document {doc_id_str}: {conflict_info['reason']}"
                )
            continue

        try:
            restored_doc = apply_recovery_inversion(
                current_doc=current_doc,
                op_type=op_type,
                source_path=src_path,
                destination_path=dst_path,
                change=change,
            )
        except PathStructureCollisionError as col_err:
            collision_info = {
                "doc_id": doc_id_str,
                "reason": f"Path collision during restoration: {col_err}",
            }
            conflicts_encountered.append(collision_info)
            if not skip_conflicting_docs:
                raise RecoveryConflictError(collision_info["reason"])
            continue

        restored_docs.append((current_doc["_id"], current_doc, restored_doc))
        restored_doc_ids.append(doc_id_str)
        restored_field_changes.append({
            "doc_id": doc_id_str,
            "pre_state": change["post_state"],
            "post_state": change["pre_state"],
        })

    # Apply updates to database
    journal_applied: List[Tuple[Any, Dict[str, Any]]] = []
    try:
        for doc_id, current_doc, new_doc in restored_docs:
            col.replace_one({"_id": doc_id}, new_doc)
            journal_applied.append((doc_id, current_doc))
    except Exception as exc:
        for doc_id, original_doc in reversed(journal_applied):
            try:
                col.replace_one({"_id": doc_id}, original_doc)
            except Exception:
                pass
        raise PyMongoError(f"Recovery failed during document update: {exc}") from exc

    # Post-recovery schema snapshot
    post_recovery_snapshot = infer_schema(
        collection_name=col_name,
        db=db,
        save_snapshot=True,
        description=f"Post-recovery snapshot for operation {operation_id}",
    )

    # Record append-only recovery provenance event
    recovery_prov = ProvenanceRecord.create_new(
        operation_id=f"rec_{operation_id}",
        operation_type=f"recovery_{op_type}",
        collection_name=col_name,
        affected_doc_ids=restored_doc_ids,
        field_changes=restored_field_changes,
        is_recovery=True,
        recovers_operation_id=operation_id,
        source_path=src_path,
        destination_path=dst_path,
        metadata={
            "pre_recovery_snapshot_id": pre_recovery_snapshot.snapshot_id,
            "post_recovery_snapshot_id": post_recovery_snapshot.snapshot_id,
            "restored_count": len(restored_doc_ids),
            "conflict_count": len(conflicts_encountered),
            "conflicts": conflicts_encountered[:10],
        },
    )
    prov_col = db[Config.COLLECTION_PROVENANCE]
    prov_col.insert_one(recovery_prov.to_dict())

    # Mark operation status in operations collection as RECOVERED
    ops_col = db[Config.COLLECTION_OPERATIONS]
    ops_col.update_one(
        {"operation_id": operation_id},
        {"$set": {
            "status": "RECOVERED",
            "recovery_provenance_id": recovery_prov.provenance_id,
        }}
    )

    save_mock_state()

    logger.info(
        "Successfully recovered operation %s in collection %s: %d documents restored, %d conflicts skipped.",
        operation_id,
        col_name,
        len(restored_doc_ids),
        len(conflicts_encountered),
    )

    return {
        "recovery_provenance_id": recovery_prov.provenance_id,
        "operation_id": operation_id,
        "collection_name": col_name,
        "restored_count": len(restored_doc_ids),
        "conflict_count": len(conflicts_encountered),
        "conflicts": conflicts_encountered,
        "restored_doc_ids": restored_doc_ids,
        "skipped_doc_ids": [c["doc_id"] for c in conflicts_encountered],
        "post_recovery_snapshot_id": post_recovery_snapshot.snapshot_id,
    }
