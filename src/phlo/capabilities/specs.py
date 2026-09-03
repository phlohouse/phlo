"""Capability specs for orchestrator-agnostic assets and checks.

Frozen dataclass contracts describing assets, checks, runs, and every
provider capability (auth, observability, table stores, catalogs, query
engines, quality backends, and more). Orchestrator adapters translate
these specs into their own primitives, so nothing here imports a
specific orchestrator.

Imported across the plugin ecosystem: orchestrator adapters (phlo-dagster) and
plugins such as phlo-clickhouse, phlo-dbt, phlo-dlt, plus delta/iceberg migrators.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from phlo.capabilities.runtime import RuntimeContext
from phlo.capabilities.support import CapabilitySupport


@dataclass(frozen=True, slots=True)
class AuthenticationProviderSpec:
    """Authentication provider capability."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class ObservabilityBackendSpec:
    """Observability backend capability (metrics, logs, dashboards, alerts)."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class PartitionSpec:
    """Partitioning metadata for an asset."""

    kind: str
    start_date: date | None = None
    timezone: str | None = None


@dataclass(frozen=True, slots=True)
class RunSpec:
    """Execution details for an asset."""

    fn: Callable[[RuntimeContext], Iterable[RunResult]]
    max_runtime_seconds: int | None = None
    max_retries: int | None = None
    retry_delay_seconds: int | None = None
    cron: str | None = None
    freshness_hours: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class AssetCheckSpec:
    """Check spec for assets, with optional execution function."""

    name: str
    asset_key: str
    fn: Callable[[RuntimeContext], CheckResult] | None = None
    blocking: bool = True
    description: str | None = None
    severity: str | None = None
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AssetSpec:
    """Orchestrator-agnostic asset specification."""

    key: str
    group: str | None
    description: str | None
    kinds: set[str] = field(default_factory=set)
    tags: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    capability_overrides: dict[str, str] = field(default_factory=dict)
    partitions: PartitionSpec | None = None
    deps: list[str] = field(default_factory=list)
    resources: set[str] = field(default_factory=set)
    run: RunSpec | None = None
    checks: list[AssetCheckSpec] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ResourceSpec:
    """Resource definitions to register with the orchestrator adapter."""

    name: str
    resource: Any


@dataclass(frozen=True, slots=True)
class TableStoreSpec:
    """Table store capability (for example Iceberg, Delta, Hudi)."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class CatalogSpec:
    """Catalog capability (for example Nessie, Hive Metastore, Glue)."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class CatalogScannerSpec:
    """Catalog scanner capability for metadata sync and table discovery."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class QueryEngineSpec:
    """Query engine capability (for example Trino, Spark SQL, DuckDB)."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class MaintenanceExecutorSpec:
    """Provider for ref-aware table maintenance execution."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class ObjectStoreSpec:
    """Object storage capability (for example MinIO, RustFS, S3)."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class SlingConnectionSpec:
    """A provider-owned Sling connection capability.

    The provider exposes ``to_sling_connection()`` returning a
    Sling-compatible connection dict, so Sling never imports another
    provider package (ADR 0047: a provider never imports another).
    """

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QualityBackendSpec:
    """Quality backend capability used by quality checks."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class MaintenanceReadModelSpec:
    """Maintenance and observability read-model capability."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class MetadataCatalogSpec:
    """Metadata catalog capability (for example OpenMetadata)."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class LineageSinkSpec:
    """Lineage sink capability (for example OpenLineage, graph store)."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class GovernanceBackendSpec:
    """Governance backend capability (for example Trino RBAC, Ranger, OPA)."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class AuthorizationPolicyBackendSpec:
    """Authorization policy backend capability (PDP for access control)."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class PublishTargetSpec:
    """Publish target capability (for example Postgres marts, warehouse export sink)."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class ApiBackendSpec:
    """API/backend capability (for example Hasura, PostgREST, custom graph API)."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class AlertSinkSpec:
    """Alert sink capability (for example PagerDuty/Slack manager)."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class SecretBackendSpec:
    """Secret backend capability (for example Vault, AWS Secrets Manager, env)."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """Single field in a normalized, provider-agnostic schema."""

    name: str
    dtype: str
    nullable: bool = True
    default: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NormalizedSchema:
    """Provider-agnostic schema representation.

    Quality providers (Pandera, Great Expectations, etc.) convert their native
    schemas into this form; storage providers consume it for diff/migration.
    """

    fields: list[FieldSpec]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SchemaChange:
    """Single field-level change detected between two schemas."""

    field_name: str
    change_type: str
    old_value: str | None = None
    new_value: str | None = None
    classification: str = "breaking"


@dataclass(frozen=True, slots=True)
class SchemaMigrationPlan:
    """Plan describing changes to apply to a table schema."""

    table_name: str
    changes: list[SchemaChange]
    classification: str
    recommendations: list[str] = field(default_factory=list)
    requires_approval: bool = False


@dataclass(frozen=True, slots=True)
class SchemaMigrationSpec:
    """Schema migration capability (registered provider)."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class WorkflowValidationSpec:
    """Workflow and schema validation capability (registered provider)."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class SchemaDiscoverySpec:
    """Schema discovery and normalization capability (registered provider)."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class NamespaceResolverSpec:
    """Default namespace resolution capability (registered provider)."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class WorkflowAuthoringSpec:
    """Workflow authoring capability (registered provider)."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class OrchestratorOperationsSpec:
    """Orchestrator operation capability (registered provider)."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class DataMigrationSourceSpec:
    """Data migration source adapter capability (registered provider)."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class MaterializeResult:
    """Result for a successful or skipped materialization."""

    metadata: dict[str, Any] = field(default_factory=dict)
    status: str | None = None


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Result for a quality or contract check."""

    check_name: str
    asset_key: str
    passed: bool
    severity: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


RunResult = MaterializeResult | CheckResult


@dataclass(frozen=True, slots=True)
class RegulatedSurfaceSpec:
    """Regulated surface capability — marks a framework surface as regulated."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SettingsStoreSpec:
    """Settings store capability for durable Observatory settings storage."""

    name: str
    provider: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    support: CapabilitySupport = field(default_factory=CapabilitySupport)


@dataclass(frozen=True, slots=True)
class UiContributionSpec:
    """UI contribution declared by a capability provider."""

    name: str
    capability_type: str
    capability_name: str
    surfaces: list[str] = field(default_factory=list)
    read_models: dict[str, str] = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)
    native_links: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
