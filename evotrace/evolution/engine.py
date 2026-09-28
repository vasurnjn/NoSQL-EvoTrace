"""Evolution execution engine.

Coordinates dry-run previews, pre-operation validation, safe atomic or journaled execution,
compensating rollback upon failure, and provenance recording.
"""

from __future__ import annotations
import copy
import logging
from typing import Any, Dict, List, Optional, Tuple
from pymongo.database import Database
from pymongo.errors import PyMongoError

from evotrace.config import Config
from evotrace.database import get_db, get_connection_status, save_mock_state
from evotrace.evolution.operations import (
    apply_add_field,
    apply_delete_field,
    apply_rename_field,
    apply_move_field,
    apply_convert_type,
)
from evotrace.evolution.validation import validate_operation_spec, EvolutionValidationError
from evotrace.models import EvolutionOperation, ProvenanceRecord
from evotrace.schema.inference import infer_schema

logger = logging.getLogger(__name__)


def transform_single_doc(
    doc: Dict[str, Any],
    operation_type: str,
    source_path: Optional[str] = None,
    destination_path: Optional[str] = None,
    default_value: Any = None,
    target_type: Optional[str] = None,
    overwrite_existing: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any], str, Optional[str]]:
    """Dispatch document transformation according to operation type."""
    if operation_type == "add_field":
        assert destination_path is not None
        return apply_add_field(doc, destination_path, default_value, overwrite_existing)
    elif operation_type == "delete_field":
        assert source_path is not None
        return apply_delete_field(doc, source_path)
    elif operation_type == "rename_field":
        assert source_path is not None and destination_path is not None
        return apply_rename_field(doc, source_path, destination_path, overwrite_existing)
    elif operation_type == "move_field":
        assert source_path is not None and destination_path is not None
        return apply_move_field(doc, source_path, destination_path, overwrite_existing)
    elif operation_type == "convert_type":
        assert source_path is not None and target_type is not None
        return apply_convert_type(doc, source_path, target_type)
    else:
        raise EvolutionValidationError(f"Unknown operation type: {operation_type}")


def preview_evolution(
    collection_name: str,
    operation_type: str,
    source_path: Optional[str] = None,
    destination_path: Optional[str] = None,
    default_value: Any = None,
    target_type: Optional[str] = None,
    overwrite_existing: bool = False,
    db: Optional[Database] = None,
) -> Dict[str, Any]:
    """Perform a dry-run preview of the planned evolution without modifying the database."""
    # 1. Syntactic & parameter validation
    validate_operation_spec(
        operation_type=operation_type,
        collection_name=collection_name,
        source_path=source_path,
        destination_path=destination_path,
        default_value=default_value,
        target_type=target_type,
    )

    db = get_db() if db is None else db
    col = db[collection_name]
    docs = list(col.find({}))
    total_docs = len(docs)

    affected_count = 0
    skipped_count = 0
    conflict_count = 0
    conflicts: List[Dict[str, Any]] = []
    samples: List[Dict[str, Any]] = []

    for doc in docs:
        doc_id = str(doc.get("_id", "unknown"))
        _, change, outcome, reason = transform_single_doc(
            doc=doc,
            operation_type=operation_type,
            source_path=source_path,
            destination_path=destination_path,
            default_value=default_value,
            target_type=target_type,
            overwrite_existing=overwrite_existing,
        )

        if outcome == "applied":
            affected_count += 1
            if len(samples) < 5:
                samples.append({
                    "doc_id": doc_id,
                    "outcome": outcome,
                    "pre_state": change["pre_state"],
                    "post_state": change["post_state"],
                })
        elif outcome == "skipped":
            skipped_count += 1
        elif outcome == "conflict":
            conflict_count += 1
            conflicts.append({
                "doc_id": doc_id,
                "reason": reason,
            })

    can_execute_cleanly = (conflict_count == 0)

    return {
        "collection_name": collection_name,
        "operation_type": operation_type,
        "source_path": source_path,
        "destination_path": destination_path,
        "default_value": default_value,
        "target_type": target_type,
        "total_documents": total_docs,
        "affected_count": affected_count,
        "skipped_count": skipped_count,
        "conflict_count": conflict_count,
        "can_execute_cleanly": can_execute_cleanly,
        "conflicts": conflicts[:20],  # cap at 20 for preview readability
        "sample_previews": samples,
    }


def execute_evolution(
    collection_name: str,
    operation_type: str,
    source_path: Optional[str] = None,
    destination_path: Optional[str] = None,
    default_value: Any = None,
    target_type: Optional[str] = None,
    skip_conflicts: bool = True,
    overwrite_existing: bool = False,
    db: Optional[Database] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Execute the evolution operation on persisted MongoDB documents.

    Maintains safe operation journaling and compensating rollback on failure.
    Records fine-grained provenance and schema snapshots before and after.

    Returns:
        (operation_dict, provenance_dict)
    """
    db = get_db() if db is None else db
    col = db[collection_name]

    # Validate parameters
    validate_operation_spec(
        operation_type=operation_type,
        collection_name=collection_name,
        source_path=source_path,
        destination_path=destination_path,
        default_value=default_value,
        target_type=target_type,
    )

    # 1. Capture pre-evolution schema snapshot
    pre_snapshot = infer_schema(
        collection_name=collection_name,
        db=db,
        save_snapshot=True,
        description=f"Pre-evolution snapshot for {operation_type}",
    )

    # 2. Initialize operation record
    operation = EvolutionOperation.create_new(
        operation_type=operation_type,
        collection_name=collection_name,
        source_path=source_path,
        destination_path=destination_path,
        default_value=default_value,
        target_type=target_type,
    )
    operation.pre_schema_version_id = pre_snapshot.snapshot_id
    operation.status = "IN_PROGRESS"

    ops_col = db[Config.COLLECTION_OPERATIONS]
    ops_col.insert_one(operation.to_dict())

    # 3. Read current documents and compute transformations
    docs = list(col.find({}))
    transformed_docs: List[Tuple[Any, Dict[str, Any], Dict[str, Any]]] = []  # (doc_id, original_doc, new_doc)
    field_changes: List[Dict[str, Any]] = []
    affected_doc_ids: List[str] = []
    conflicts_encountered: List[Dict[str, Any]] = []
    skipped_count = 0

    for doc in docs:
        doc_id = doc.get("_id")
        str_id = str(doc_id)

        new_doc, change, outcome, reason = transform_single_doc(
            doc=doc,
            operation_type=operation_type,
            source_path=source_path,
            destination_path=destination_path,
            default_value=default_value,
            target_type=target_type,
            overwrite_existing=overwrite_existing,
        )

        if outcome == "applied":
            transformed_docs.append((doc_id, doc, new_doc))
            field_changes.append(change)
            affected_doc_ids.append(str_id)
        elif outcome == "skipped":
            skipped_count += 1
        elif outcome == "conflict":
            conflicts_encountered.append({"doc_id": str_id, "reason": reason})
            if not skip_conflicts:
                # Abort execution before any DB mutation
                operation.status = "FAILED"
                operation.error_message = f"Aborted due to conflict on document {str_id}: {reason}"
                ops_col.update_one({"operation_id": operation.operation_id}, {"$set": operation.to_dict()})
                raise EvolutionValidationError(operation.error_message)

    # 4. Apply mutations to MongoDB with Safe Journaling and Compensating Rollback
    journal_applied: List[Tuple[Any, Dict[str, Any]]] = []  # (doc_id, original_doc)
    conn_status = get_connection_status()
    use_native_tx = conn_status.get("supports_transactions", False) and hasattr(db.client, "start_session")

    try:
        if use_native_tx:
            with db.client.start_session() as session:
                with session.start_transaction():
                    for doc_id, original_doc, new_doc in transformed_docs:
                        col.replace_one({"_id": doc_id}, new_doc, session=session)
        else:
            # Standalone MongoDB or embedded mock: use safe journal & compensation
            for doc_id, original_doc, new_doc in transformed_docs:
                col.replace_one({"_id": doc_id}, new_doc)
                journal_applied.append((doc_id, original_doc))

    except Exception as exc:
        logger.error(
            "Error executing evolution on collection %s: %s. Initiating compensating rollback.",
            collection_name,
            exc,
        )
        # Compensating rollback: restore original documents
        rollback_errors = 0
        for doc_id, original_doc in reversed(journal_applied):
            try:
                col.replace_one({"_id": doc_id}, original_doc)
            except Exception as rb_exc:
                rollback_errors += 1
                logger.critical("Compensating rollback failed for doc %s: %s", doc_id, rb_exc)

        operation.status = "FAILED"
        operation.error_message = f"Execution failed: {exc}. Rollback performed on {len(journal_applied)} documents."
        ops_col.update_one({"operation_id": operation.operation_id}, {"$set": operation.to_dict()})
        raise PyMongoError(operation.error_message) from exc

    # 5. Capture post-evolution schema snapshot
    post_snapshot = infer_schema(
        collection_name=collection_name,
        db=db,
        save_snapshot=True,
        operation_id=operation.operation_id,
        description=f"Post-evolution snapshot for {operation_type} (Op {operation.operation_id})",
    )

    # 6. Record fine-grained append-only provenance
    provenance = ProvenanceRecord.create_new(
        operation_id=operation.operation_id,
        operation_type=operation_type,
        collection_name=collection_name,
        affected_doc_ids=affected_doc_ids,
        field_changes=field_changes,
        is_recovery=False,
        recovers_operation_id=None,
        source_path=source_path,
        destination_path=destination_path,
        metadata={
            "pre_schema_version_id": pre_snapshot.snapshot_id,
            "post_schema_version_id": post_snapshot.snapshot_id,
            "total_documents": len(docs),
            "affected_count": len(affected_doc_ids),
            "skipped_count": skipped_count,
            "conflict_count": len(conflicts_encountered),
        },
    )
    prov_col = db[Config.COLLECTION_PROVENANCE]
    prov_col.insert_one(provenance.to_dict())

    # 7. Update operation record as EXECUTED
    operation.status = "EXECUTED"
    operation.executed_at = provenance.timestamp
    operation.post_schema_version_id = post_snapshot.snapshot_id
    operation.summary = {
        "total_documents": len(docs),
        "affected_count": len(affected_doc_ids),
        "skipped_count": skipped_count,
        "conflict_count": len(conflicts_encountered),
        "conflicts": conflicts_encountered[:10],
        "provenance_id": provenance.provenance_id,
    }

    ops_col.update_one({"operation_id": operation.operation_id}, {"$set": operation.to_dict()})

    save_mock_state()

    logger.info(
        "Evolution operation %s (%s) executed successfully on %d documents in %s.",
        operation.operation_id,
        operation_type,
        len(affected_doc_ids),
        collection_name,
    )

    return operation.to_dict(), provenance.to_dict()
