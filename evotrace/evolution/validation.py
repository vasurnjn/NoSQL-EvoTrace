"""Validation rules and safety checks for schema evolution operations."""

from __future__ import annotations
from datetime import datetime
import re
from typing import Any, Dict, List, Optional, Tuple

from evotrace.config import Config
from evotrace.evolution.paths import get_nested_path, paths_overlap


class EvolutionValidationError(Exception):
    """Raised when an evolution operation specification is invalid or unsafe."""
    pass


SUPPORTED_OPERATIONS = {
    "add_field",
    "delete_field",
    "rename_field",
    "move_field",
    "convert_type",
}

SUPPORTED_TYPE_TRANSVERSIONS = {
    ("string", "integer"),
    ("integer", "string"),
    ("string", "double"),
    ("double", "string"),
    ("double", "integer"),
    ("integer", "double"),
    ("string", "boolean"),
    ("boolean", "string"),
    ("string", "datetime"),
    ("datetime", "string"),
}

VALID_PATH_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*(\.[a-zA-Z_][a-zA-Z0-9_-]*)*$")


def validate_path_syntax(path: Optional[str], field_label: str = "Path") -> None:
    """Ensure the path string adheres to standard alphanumeric dot-notated syntax."""
    if not path or not path.strip():
        raise EvolutionValidationError(f"{field_label} cannot be empty.")
    cleaned = path.strip()
    if cleaned == "_id":
        raise EvolutionValidationError(f"Cannot mutate or remove primary identifier field '_id'.")
    if not VALID_PATH_PATTERN.match(cleaned):
        raise EvolutionValidationError(
            f"{field_label} '{cleaned}' is not a valid dot-notated identifier path. "
            "Paths must contain alphanumeric/underscore segments separated by dots."
        )


def validate_safe_type_conversion(val: Any, target_type: str) -> Tuple[bool, Any, Optional[str]]:
    """Validate and attempt a safe, non-destructive type conversion.

    Returns:
        (success, converted_value, error_message)
    """
    if val is None:
        # Null values remain null across type conversions
        return True, None, None

    if target_type == "integer":
        if isinstance(val, int) and not isinstance(val, bool):
            return True, val, None
        if isinstance(val, float):
            if val.is_integer():
                return True, int(val), None
            return False, None, f"Lossy conversion: Float {val} has non-zero fractional part, cannot convert to integer without truncation."
        if isinstance(val, str):
            val_clean = val.strip()
            try:
                # Disallow strings like '12.34' for direct integer parsing
                return True, int(val_clean), None
            except ValueError:
                return False, None, f"String '{val}' cannot be parsed as a strict integer."
        return False, None, f"Cannot convert type '{type(val).__name__}' to integer."

    elif target_type == "double":
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return True, float(val), None
        if isinstance(val, str):
            try:
                return True, float(val.strip()), None
            except ValueError:
                return False, None, f"String '{val}' cannot be parsed as double."
        return False, None, f"Cannot convert type '{type(val).__name__}' to double."

    elif target_type == "string":
        if isinstance(val, (int, float, bool)):
            return True, str(val).lower() if isinstance(val, bool) else str(val), None
        if isinstance(val, str):
            return True, val, None
        if isinstance(val, datetime):
            return True, val.isoformat(), None
        return False, None, f"Cannot safely convert complex type '{type(val).__name__}' to string."

    elif target_type == "boolean":
        if isinstance(val, bool):
            return True, val, None
        if isinstance(val, str):
            cleaned = val.strip().lower()
            if cleaned in ("true", "1", "yes", "t"):
                return True, True, None
            if cleaned in ("false", "0", "no", "f"):
                return True, False, None
            return False, None, f"String '{val}' is not a recognized boolean literal ('true'/'false')."
        if isinstance(val, int) and val in (0, 1):
            return True, bool(val), None
        return False, None, f"Cannot safely convert type '{type(val).__name__}' to boolean."

    elif target_type == "datetime":
        if isinstance(val, datetime):
            return True, val, None
        if isinstance(val, str):
            try:
                # Parse ISO format
                parsed = datetime.fromisoformat(val.replace("Z", "+00:00"))
                return True, parsed, None
            except (ValueError, TypeError):
                return False, None, f"String '{val}' is not valid ISO 8601 datetime format."
        return False, None, f"Cannot convert type '{type(val).__name__}' to datetime."

    return False, None, f"Target type '{target_type}' is not supported for safe conversion."


def validate_operation_spec(
    operation_type: str,
    collection_name: str,
    source_path: Optional[str] = None,
    destination_path: Optional[str] = None,
    default_value: Any = None,
    target_type: Optional[str] = None,
) -> None:
    """Perform structural and semantic validation on an operation specification before scanning data."""
    if not collection_name or not collection_name.strip():
        raise EvolutionValidationError("Collection name must be specified.")

    clean_col = collection_name.strip()
    if clean_col in Config.get_system_collections():
        raise EvolutionValidationError(
            f"Collection '{clean_col}' is a reserved EvoTrace system collection and cannot be mutated."
        )

    if operation_type not in SUPPORTED_OPERATIONS:
        raise EvolutionValidationError(
            f"Unsupported operation type '{operation_type}'. Must be one of: {sorted(SUPPORTED_OPERATIONS)}"
        )

    if operation_type == "add_field":
        validate_path_syntax(destination_path, "Destination path")

    elif operation_type == "delete_field":
        validate_path_syntax(source_path, "Source path")

    elif operation_type in ("rename_field", "move_field"):
        validate_path_syntax(source_path, "Source path")
        validate_path_syntax(destination_path, "Destination path")
        if source_path.strip() == destination_path.strip():
            raise EvolutionValidationError("Source path and destination path cannot be identical.")
        if operation_type == "rename_field":
            # For rename, verify it is renaming within the same parent or both top-level
            src_parent = source_path.rsplit(".", 1)[0] if "." in source_path else ""
            dst_parent = destination_path.rsplit(".", 1)[0] if "." in destination_path else ""
            if src_parent != dst_parent:
                raise EvolutionValidationError(
                    f"Renaming across different parent hierarchies (from '{src_parent or 'root'}' "
                    f"to '{dst_parent or 'root'}') should use 'move_field' rather than 'rename_field'."
                )

    elif operation_type == "convert_type":
        validate_path_syntax(source_path, "Source path")
        if not target_type or target_type not in ("integer", "double", "string", "boolean", "datetime"):
            raise EvolutionValidationError(
                f"Invalid target type '{target_type}'. Supported: integer, double, string, boolean, datetime"
            )
