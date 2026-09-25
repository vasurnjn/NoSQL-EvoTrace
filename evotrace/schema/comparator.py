"""Schema snapshot comparator.

Analyzes differences between two observed schema versions, detecting added fields,
removed fields, type transitions, mixed-type evolution, and presence ratio shifts.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional


def compare_snapshots(snap_a: Dict[str, Any], snap_b: Dict[str, Any]) -> Dict[str, Any]:
    """Compare two schema snapshot dictionaries and generate a structural diff."""
    fields_a = snap_a.get("fields", {})
    fields_b = snap_b.get("fields", {})

    paths_a = set(fields_a.keys())
    paths_b = set(fields_b.keys())

    added_paths = sorted(list(paths_b - paths_a))
    removed_paths = sorted(list(paths_a - paths_b))
    common_paths = sorted(list(paths_a & paths_b))

    modified_fields: List[Dict[str, Any]] = []
    unmodified_fields: List[str] = []

    for path in common_paths:
        fa = fields_a[path]
        fb = fields_b[path]

        types_a = fa.get("types", {})
        types_b = fb.get("types", {})
        ratio_a = fa.get("presence_ratio", 0.0)
        ratio_b = fb.get("presence_ratio", 0.0)
        mixed_a = fa.get("is_mixed_type", False)
        mixed_b = fb.get("is_mixed_type", False)

        has_type_change = set(types_a.keys()) != set(types_b.keys())
        has_ratio_change = abs(ratio_a - ratio_b) > 0.0001
        has_mixed_change = mixed_a != mixed_b

        if has_type_change or has_ratio_change or has_mixed_change:
            modified_fields.append({
                "path": path,
                "pre_types": types_a,
                "post_types": types_b,
                "pre_presence_ratio": ratio_a,
                "post_presence_ratio": ratio_b,
                "pre_mixed": mixed_a,
                "post_mixed": mixed_b,
                "has_type_change": has_type_change,
                "has_ratio_change": has_ratio_change,
            })
        else:
            unmodified_fields.append(path)

    doc_count_a = snap_a.get("document_count", 0)
    doc_count_b = snap_b.get("document_count", 0)
    fingerprint_a = snap_a.get("schema_fingerprint", "")
    fingerprint_b = snap_b.get("schema_fingerprint", "")

    is_identical = fingerprint_a == fingerprint_b

    return {
        "is_identical": is_identical,
        "snapshot_a_id": snap_a.get("snapshot_id"),
        "snapshot_b_id": snap_b.get("snapshot_id"),
        "created_at_a": snap_a.get("created_at"),
        "created_at_b": snap_b.get("created_at"),
        "fingerprint_a": fingerprint_a,
        "fingerprint_b": fingerprint_b,
        "document_count_a": doc_count_a,
        "document_count_b": doc_count_b,
        "document_count_diff": doc_count_b - doc_count_a,
        "added_fields": [fields_b[p] for p in added_paths],
        "removed_fields": [fields_a[p] for p in removed_paths],
        "modified_fields": modified_fields,
        "unmodified_field_paths": unmodified_fields,
        "stats": {
            "total_added": len(added_paths),
            "total_removed": len(removed_paths),
            "total_modified": len(modified_fields),
            "total_unmodified": len(unmodified_fields),
        },
    }
