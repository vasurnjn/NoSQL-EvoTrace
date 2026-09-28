"""Schema evolution and transformation package for NoSQL-EvoTrace."""

from evotrace.evolution.paths import (
    get_nested_path,
    set_nested_path,
    delete_nested_path,
    path_exists,
    split_path,
    is_ancestor_path,
    is_descendant_path,
    paths_overlap,
    PathStructureCollisionError,
)
from evotrace.evolution.validation import (
    validate_operation_spec,
    validate_path_syntax,
    validate_safe_type_conversion,
    EvolutionValidationError,
    SUPPORTED_OPERATIONS,
)
from evotrace.evolution.operations import (
    apply_add_field,
    apply_delete_field,
    apply_rename_field,
    apply_move_field,
    apply_convert_type,
    compute_value_fingerprint,
)
from evotrace.evolution.engine import (
    preview_evolution,
    execute_evolution,
)

__all__ = [
    "get_nested_path",
    "set_nested_path",
    "delete_nested_path",
    "path_exists",
    "split_path",
    "is_ancestor_path",
    "is_descendant_path",
    "paths_overlap",
    "PathStructureCollisionError",
    "validate_operation_spec",
    "validate_path_syntax",
    "validate_safe_type_conversion",
    "EvolutionValidationError",
    "SUPPORTED_OPERATIONS",
    "apply_add_field",
    "apply_delete_field",
    "apply_rename_field",
    "apply_move_field",
    "apply_convert_type",
    "compute_value_fingerprint",
    "preview_evolution",
    "execute_evolution",
]
