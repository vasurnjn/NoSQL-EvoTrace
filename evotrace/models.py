"""Domain models and data representations for NoSQL-EvoTrace."""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class FieldStats:
    """Observed statistics for a specific document path."""
    path: str
    types: Dict[str, int] = field(default_factory=dict)
    presence_count: int = 0
    null_count: int = 0
    missing_count: int = 0
    presence_ratio: float = 0.0
    is_mixed_type: bool = False
    samples: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SchemaSnapshot:
    """Point-in-time observed schema structure for a collection."""
    snapshot_id: str
    collection_name: str
    schema_fingerprint: str
    created_at: str
    document_count: int
    fields: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    operation_id: Optional[str] = None
    description: str = ""

    @classmethod
    def create_new(
        cls,
        collection_name: str,
        fingerprint: str,
        document_count: int,
        fields_data: Dict[str, Dict[str, Any]],
        operation_id: Optional[str] = None,
        description: str = "",
    ) -> SchemaSnapshot:
        return cls(
            snapshot_id=f"snap_{uuid.uuid4().hex[:12]}",
            collection_name=collection_name,
            schema_fingerprint=fingerprint,
            created_at=utc_now_iso(),
            document_count=document_count,
            fields=fields_data,
            operation_id=operation_id,
            description=description,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EvolutionOperation:
    """Record of an executed or pending schema evolution mutation."""
    operation_id: str
    operation_type: str  # add_field, delete_field, rename_field, move_field, convert_type
    collection_name: str
    source_path: Optional[str] = None
    destination_path: Optional[str] = None
    default_value: Any = None
    target_type: Optional[str] = None
    pre_schema_version_id: Optional[str] = None
    post_schema_version_id: Optional[str] = None
    status: str = "PENDING"  # PENDING, PREVIEW, EXECUTED, FAILED, RECOVERED
    created_at: str = field(default_factory=utc_now_iso)
    executed_at: Optional[str] = None
    summary: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None

    @classmethod
    def create_new(
        cls,
        operation_type: str,
        collection_name: str,
        source_path: Optional[str] = None,
        destination_path: Optional[str] = None,
        default_value: Any = None,
        target_type: Optional[str] = None,
    ) -> EvolutionOperation:
        return cls(
            operation_id=f"op_{uuid.uuid4().hex[:12]}",
            operation_type=operation_type,
            collection_name=collection_name,
            source_path=source_path,
            destination_path=destination_path,
            default_value=default_value,
            target_type=target_type,
            created_at=utc_now_iso(),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DocumentFieldChange:
    """Granular field-level provenance record for a single document."""
    doc_id: str
    pre_state: Dict[str, Any]   # exists: bool, is_null: bool, value: Any, type: str, fingerprint: str
    post_state: Dict[str, Any]  # exists: bool, is_null: bool, value: Any, type: str, fingerprint: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProvenanceRecord:
    """Append-only audit and provenance event for an evolution operation."""
    provenance_id: str
    operation_id: str
    operation_type: str
    collection_name: str
    timestamp: str
    affected_doc_ids: List[str]
    field_changes: List[Dict[str, Any]]  # List of DocumentFieldChange dicts
    is_recovery: bool = False
    recovers_operation_id: Optional[str] = None
    source_path: Optional[str] = None
    destination_path: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create_new(
        cls,
        operation_id: str,
        operation_type: str,
        collection_name: str,
        affected_doc_ids: List[str],
        field_changes: List[Dict[str, Any]],
        is_recovery: bool = False,
        recovers_operation_id: Optional[str] = None,
        source_path: Optional[str] = None,
        destination_path: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ProvenanceRecord:
        return cls(
            provenance_id=f"prov_{uuid.uuid4().hex[:12]}",
            operation_id=operation_id,
            operation_type=operation_type,
            collection_name=collection_name,
            timestamp=utc_now_iso(),
            affected_doc_ids=affected_doc_ids,
            field_changes=field_changes,
            is_recovery=is_recovery,
            recovers_operation_id=recovers_operation_id,
            source_path=source_path,
            destination_path=destination_path,
            metadata=metadata or {},
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RegisteredQuery:
    """User-registered application query filter with declared path dependencies."""
    query_id: str
    name: str
    collection_name: str
    description: str
    filter_spec: Dict[str, Any]
    referenced_paths: List[str]
    enabled: bool = True
    created_at: str = field(default_factory=utc_now_iso)
    last_run_at: Optional[str] = None
    last_result_count: Optional[int] = None

    @classmethod
    def create_new(
        cls,
        name: str,
        collection_name: str,
        description: str,
        filter_spec: Dict[str, Any],
        referenced_paths: List[str],
        enabled: bool = True,
    ) -> RegisteredQuery:
        return cls(
            query_id=f"qry_{uuid.uuid4().hex[:12]}",
            name=name,
            collection_name=collection_name,
            description=description,
            filter_spec=filter_spec,
            referenced_paths=referenced_paths,
            enabled=enabled,
            created_at=utc_now_iso(),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExperimentResult:
    """Persisted outcome of a benchmark evaluation scenario."""
    experiment_id: str
    scenario_name: str
    dataset_size: int
    parameters: Dict[str, Any]
    metrics: Dict[str, Any]
    timestamp: str = field(default_factory=utc_now_iso)
    environment_info: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
