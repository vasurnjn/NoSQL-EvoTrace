"""Document transformation algorithms and state transition calculators."""

from __future__ import annotations
import copy
from datetime import datetime
import hashlib
import json
from typing import Any, Dict, Optional, Tuple

from evotrace.evolution.paths import (
    delete_nested_path,
    get_nested_path,
    path_exists,
    set_nested_path,
    PathStructureCollisionError,
)
from evotrace.evolution.validation import validate_safe_type_conversion
from evotrace.schema.inference import map_bson_python_type


def compute_value_fingerprint(value: Any, exists: bool) -> str:
    """Compute a deterministic hash for a field's value state."""
    if not exists:
        return "FINGERPRINT_ABSENT"
    if value is None:
        return "FINGERPRINT_NULL"

    # Normalize value representation
    if isinstance(value, datetime):
        serialized = f"datetime:{value.isoformat()}"
    elif isinstance(value, (dict, list)):
        try:
            serialized = f"json:{json.dumps(value, sort_keys=True, default=str)}"
        except Exception:
            serialized = f"str:{str(value)}"
    else:
        serialized = f"{type(value).__name__}:{str(value)}"

    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def apply_add_field(
    doc: Dict[str, Any],
    destination_path: str,
    default_value: Any,
    overwrite_existing: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any], str, Optional[str]]:
    """Apply AddField transformation to a single document.

    Returns:
        (updated_doc, change_dict, outcome, message)
        outcome: 'applied' | 'skipped' | 'conflict'
    """
    new_doc = copy.deepcopy(doc)
    doc_id = str(doc.get("_id", "unknown"))

    pre_val, pre_exists, pre_null = get_nested_path(doc, destination_path)
    pre_state = {
        "exists": pre_exists,
        "is_null": pre_null,
        "value": pre_val,
        "type": map_bson_python_type(pre_val) if pre_exists else "none",
        "fingerprint": compute_value_fingerprint(pre_val, pre_exists),
    }

    if pre_exists and not overwrite_existing:
        return (
            doc,
            {
                "doc_id": doc_id,
                "pre_state": pre_state,
                "post_state": pre_state,
            },
            "skipped",
            f"Destination path '{destination_path}' already exists in document.",
        )

    try:
        set_nested_path(new_doc, destination_path, copy.deepcopy(default_value))
    except PathStructureCollisionError as exc:
        return (
            doc,
            {
                "doc_id": doc_id,
                "pre_state": pre_state,
                "post_state": pre_state,
            },
            "conflict",
            str(exc),
        )

    post_val, post_exists, post_null = get_nested_path(new_doc, destination_path)
    post_state = {
        "exists": post_exists,
        "is_null": post_null,
        "value": post_val,
        "type": map_bson_python_type(post_val),
        "fingerprint": compute_value_fingerprint(post_val, post_exists),
    }

    return (
        new_doc,
        {
            "doc_id": doc_id,
            "pre_state": pre_state,
            "post_state": post_state,
        },
        "applied",
        None,
    )


def apply_delete_field(
    doc: Dict[str, Any],
    source_path: str,
) -> Tuple[Dict[str, Any], Dict[str, Any], str, Optional[str]]:
    """Apply DeleteField transformation to a single document."""
    new_doc = copy.deepcopy(doc)
    doc_id = str(doc.get("_id", "unknown"))

    pre_val, pre_exists, pre_null = get_nested_path(doc, source_path)
    pre_state = {
        "exists": pre_exists,
        "is_null": pre_null,
        "value": pre_val,
        "type": map_bson_python_type(pre_val) if pre_exists else "none",
        "fingerprint": compute_value_fingerprint(pre_val, pre_exists),
    }

    if not pre_exists:
        return (
            doc,
            {
                "doc_id": doc_id,
                "pre_state": pre_state,
                "post_state": pre_state,
            },
            "skipped",
            f"Source path '{source_path}' does not exist in document.",
        )

    delete_nested_path(new_doc, source_path, clean_empty_parents=True)

    post_state = {
        "exists": False,
        "is_null": False,
        "value": None,
        "type": "none",
        "fingerprint": compute_value_fingerprint(None, False),
    }

    return (
        new_doc,
        {
            "doc_id": doc_id,
            "pre_state": pre_state,
            "post_state": post_state,
        },
        "applied",
        None,
    )


def apply_rename_field(
    doc: Dict[str, Any],
    source_path: str,
    destination_path: str,
    overwrite_existing: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any], str, Optional[str]]:
    """Apply RenameField transformation within the same scope."""
    new_doc = copy.deepcopy(doc)
    doc_id = str(doc.get("_id", "unknown"))

    pre_val, pre_exists, pre_null = get_nested_path(doc, source_path)
    pre_state = {
        "exists": pre_exists,
        "is_null": pre_null,
        "value": pre_val,
        "type": map_bson_python_type(pre_val) if pre_exists else "none",
        "fingerprint": compute_value_fingerprint(pre_val, pre_exists),
    }

    if not pre_exists:
        return (
            doc,
            {
                "doc_id": doc_id,
                "pre_state": pre_state,
                "post_state": pre_state,
            },
            "skipped",
            f"Source path '{source_path}' does not exist in document.",
        )

    dst_val, dst_exists, dst_null = get_nested_path(doc, destination_path)
    if dst_exists and not overwrite_existing:
        return (
            doc,
            {
                "doc_id": doc_id,
                "pre_state": pre_state,
                "post_state": pre_state,
            },
            "conflict",
            f"Destination path '{destination_path}' already exists in document.",
        )

    # Delete source and set destination
    val, _ = delete_nested_path(new_doc, source_path, clean_empty_parents=False)
    try:
        set_nested_path(new_doc, destination_path, val)
    except PathStructureCollisionError as exc:
        return (
            doc,
            {
                "doc_id": doc_id,
                "pre_state": pre_state,
                "post_state": pre_state,
            },
            "conflict",
            str(exc),
        )

    post_state = {
        "exists": True,
        "is_null": pre_null,
        "value": val,
        "type": map_bson_python_type(val),
        "fingerprint": compute_value_fingerprint(val, True),
    }

    return (
        new_doc,
        {
            "doc_id": doc_id,
            "pre_state": pre_state,
            "post_state": post_state,
        },
        "applied",
        None,
    )


def apply_move_field(
    doc: Dict[str, Any],
    source_path: str,
    destination_path: str,
    overwrite_existing: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any], str, Optional[str]]:
    """Apply MoveField (flat-to-nested or nested-to-flat) transformation."""
    new_doc = copy.deepcopy(doc)
    doc_id = str(doc.get("_id", "unknown"))

    src_val, src_exists, src_null = get_nested_path(doc, source_path)
    pre_state = {
        "exists": src_exists,
        "is_null": src_null,
        "value": src_val,
        "type": map_bson_python_type(src_val) if src_exists else "none",
        "fingerprint": compute_value_fingerprint(src_val, src_exists),
    }

    if not src_exists:
        return (
            doc,
            {
                "doc_id": doc_id,
                "pre_state": pre_state,
                "post_state": pre_state,
            },
            "skipped",
            f"Source path '{source_path}' does not exist in document.",
        )

    dst_val, dst_exists, dst_null = get_nested_path(doc, destination_path)
    if dst_exists and not overwrite_existing:
        return (
            doc,
            {
                "doc_id": doc_id,
                "pre_state": pre_state,
                "post_state": pre_state,
            },
            "conflict",
            f"Destination path '{destination_path}' already exists in document.",
        )

    # Remove source, cleaning empty parent structures
    moved_value, _ = delete_nested_path(new_doc, source_path, clean_empty_parents=True)
    try:
        set_nested_path(new_doc, destination_path, moved_value)
    except PathStructureCollisionError as exc:
        return (
            doc,
            {
                "doc_id": doc_id,
                "pre_state": pre_state,
                "post_state": pre_state,
            },
            "conflict",
            str(exc),
        )

    post_state = {
        "exists": True,
        "is_null": src_null,
        "value": moved_value,
        "type": map_bson_python_type(moved_value),
        "fingerprint": compute_value_fingerprint(moved_value, True),
    }

    return (
        new_doc,
        {
            "doc_id": doc_id,
            "pre_state": pre_state,
            "post_state": post_state,
        },
        "applied",
        None,
    )


def apply_convert_type(
    doc: Dict[str, Any],
    source_path: str,
    target_type: str,
) -> Tuple[Dict[str, Any], Dict[str, Any], str, Optional[str]]:
    """Apply safe type conversion on source path value."""
    new_doc = copy.deepcopy(doc)
    doc_id = str(doc.get("_id", "unknown"))

    src_val, src_exists, src_null = get_nested_path(doc, source_path)
    pre_state = {
        "exists": src_exists,
        "is_null": src_null,
        "value": src_val,
        "type": map_bson_python_type(src_val) if src_exists else "none",
        "fingerprint": compute_value_fingerprint(src_val, src_exists),
    }

    if not src_exists:
        return (
            doc,
            {
                "doc_id": doc_id,
                "pre_state": pre_state,
                "post_state": pre_state,
            },
            "skipped",
            f"Source path '{source_path}' does not exist in document.",
        )

    success, converted_val, err_msg = validate_safe_type_conversion(src_val, target_type)
    if not success:
        return (
            doc,
            {
                "doc_id": doc_id,
                "pre_state": pre_state,
                "post_state": pre_state,
            },
            "conflict",
            err_msg or "Type conversion rejected.",
        )

    set_nested_path(new_doc, source_path, converted_val)

    post_state = {
        "exists": True,
        "is_null": converted_val is None,
        "value": converted_val,
        "type": map_bson_python_type(converted_val),
        "fingerprint": compute_value_fingerprint(converted_val, True),
    }

    return (
        new_doc,
        {
            "doc_id": doc_id,
            "pre_state": pre_state,
            "post_state": post_state,
        },
        "applied",
        None,
    )
