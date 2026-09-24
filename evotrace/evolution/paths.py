"""Nested path utilities for MongoDB-style dot-notated document fields.

Handles hierarchical path traversal, insertion, deletion, collision detection,
and ancestor/descendant relationship calculations.
"""

from __future__ import annotations
import copy
from typing import Any, Dict, List, Optional, Tuple


class PathStructureCollisionError(Exception):
    """Raised when setting a nested path encounters an incompatible parent value (e.g. primitive)."""
    pass


def split_path(path: str) -> Tuple[Optional[str], str]:
    """Split a dot-notated path into its parent path and leaf key.

    Examples:
        'address.city' -> ('address', 'city')
        'status' -> (None, 'status')
    """
    cleaned = path.strip()
    if "." not in cleaned:
        return None, cleaned
    parts = cleaned.rsplit(".", 1)
    return parts[0], parts[1]


def get_nested_path(doc: Dict[str, Any], path: str) -> Tuple[Any, bool, bool]:
    """Retrieve value at nested dot path.

    Returns:
        (value, exists, is_null):
            value: The retrieved value (or None if missing)
            exists: True if path was present in doc
            is_null: True if path was present and value was None
    """
    if not path or not isinstance(doc, dict):
        return None, False, False

    parts = path.split(".")
    curr: Any = doc

    for i, part in enumerate(parts):
        if not isinstance(curr, dict) or part not in curr:
            return None, False, False
        curr = curr[part]

    return curr, True, curr is None


def path_exists(doc: Dict[str, Any], path: str) -> bool:
    """Return whether path exists in document."""
    _, exists, _ = get_nested_path(doc, path)
    return exists


def set_nested_path(doc: Dict[str, Any], path: str, value: Any) -> None:
    """Set value at nested dot path in doc in-place.

    Creates intermediate dictionaries as necessary.
    Raises PathStructureCollisionError if any intermediate node is not a dictionary.
    """
    parts = path.split(".")
    curr: Any = doc

    for i, part in enumerate(parts[:-1]):
        if part not in curr:
            curr[part] = {}
        elif not isinstance(curr[part], dict):
            ancestor_path = ".".join(parts[: i + 1])
            raise PathStructureCollisionError(
                f"Path collision: Intermediate path '{ancestor_path}' already exists as "
                f"type '{type(curr[part]).__name__}', cannot treat as nested dictionary."
            )
        curr = curr[part]

    leaf = parts[-1]
    curr[leaf] = value


def delete_nested_path(
    doc: Dict[str, Any],
    path: str,
    clean_empty_parents: bool = True,
) -> Tuple[Any, bool]:
    """Delete value at nested dot path.

    If clean_empty_parents is True, recursively deletes intermediate empty dictionaries.
    Returns:
        (deleted_value, existed)
    """
    parts = path.split(".")
    curr: Any = doc
    stack: List[Tuple[Dict[str, Any], str]] = []

    for part in parts[:-1]:
        if not isinstance(curr, dict) or part not in curr:
            return None, False
        stack.append((curr, part))
        curr = curr[part]

    leaf = parts[-1]
    if not isinstance(curr, dict) or leaf not in curr:
        return None, False

    deleted_value = curr.pop(leaf)

    if clean_empty_parents:
        # Walk up stack and remove empty parent dictionaries
        for parent_dict, parent_key in reversed(stack):
            child = parent_dict.get(parent_key)
            if isinstance(child, dict) and len(child) == 0:
                parent_dict.pop(parent_key, None)
            else:
                break

    return deleted_value, True


def is_ancestor_path(ancestor: str, descendant: str) -> bool:
    """Return True if 'ancestor' is a strict parent path of 'descendant'.

    Example: 'address' is ancestor of 'address.city'
    """
    if ancestor == descendant:
        return False
    return descendant.startswith(ancestor + ".")


def is_descendant_path(descendant: str, ancestor: str) -> bool:
    """Return True if 'descendant' is a strict child path of 'ancestor'."""
    return is_ancestor_path(ancestor, descendant)


def paths_overlap(path_a: str, path_b: str) -> bool:
    """Return True if path_a equals path_b or one is an ancestor of the other."""
    return path_a == path_b or is_ancestor_path(path_a, path_b) or is_ancestor_path(path_b, path_a)
