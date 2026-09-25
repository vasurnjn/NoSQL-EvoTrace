"""Schema analysis and comparison package for NoSQL-EvoTrace."""

from evotrace.schema.inference import (
    extract_document_paths,
    infer_schema,
    get_latest_snapshot,
    get_snapshot_history,
    get_snapshot_by_id,
    compute_schema_fingerprint,
    map_bson_python_type,
)
from evotrace.schema.comparator import compare_snapshots

__all__ = [
    "extract_document_paths",
    "infer_schema",
    "get_latest_snapshot",
    "get_snapshot_history",
    "get_snapshot_by_id",
    "compute_schema_fingerprint",
    "map_bson_python_type",
    "compare_snapshots",
]
