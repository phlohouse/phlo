"""Runtime capability interfaces used by capability providers.

Declares the Protocol contracts every provider-backed capability must satisfy
(table stores, versioned catalogs, governance, authn/authz, orchestration,
query engines, maintenance, lineage sinks) plus the neutral dataclasses
exchanged across them.
Imported by phlo-api (observatory lineage), phlo-dagster (framework definitions),
and phlo-delta; builds its object inventory on phlo.capabilities.inventory.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from phlo.capabilities.inventory import ObjectInventory


@dataclass(frozen=True, slots=True)
class TableStoreSupport:
    """Explicit support metadata for table-store adapters."""

    supports_refs: bool = False
    partition_transforms: frozenset[str] = frozenset({"identity"})
    supports_snapshots: bool = False
    supports_compaction: bool = False
    supports_vacuum: bool = False

    def supports_partition_transform(self, transform: str) -> bool:
        return transform in self.partition_transforms


@dataclass(frozen=True, slots=True)
class TableStateObservation:
    """Provider-neutral table readback returned by an optional observer."""

    state: str
    revision: str | None = None
    schema_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class TableStateObserver(Protocol):
    """Optional capability for authoritative table-state readback."""

    def observe_table_state(
        self, *, table_name: str, override_ref: str | None = None
    ) -> TableStateObservation | dict[str, Any]:
        """Return present, absent, or unavailable normalized table state."""
        ...


@runtime_checkable
class TableStore(Protocol):
    """Protocol for table-store providers used by ingestion components.

    Required methods: ``ensure_table``, ``append_parquet``, ``merge_parquet``.
    Extended operations (``overwrite_parquet``, ``delete_rows``, ``compact``,
    ``list_snapshots``, ``rollback_to_snapshot``, ``vacuum``) raise
    ``NotImplementedError`` by default so providers opt in incrementally.
    """

    @property
    def support(self) -> TableStoreSupport:
        """Return explicit support metadata for this table-store adapter."""
        return TableStoreSupport()

    def ensure_table(
        self,
        *,
        table_name: str,
        schema: Any,
        partition_spec: Any = None,
        override_ref: str | None = None,
    ) -> Any:
        """Ensure a destination table exists."""

    def append_parquet(
        self,
        *,
        table_name: str,
        data_path: str | Path,
        override_ref: str | None = None,
    ) -> dict[str, int]:
        """Append staged parquet data to a destination table."""

    def merge_parquet(
        self,
        *,
        table_name: str,
        data_path: str | Path,
        unique_key: str,
        override_ref: str | None = None,
        deduplication_method: str | None = None,
        deduplication_order_by: str | None = None,
    ) -> dict[str, int]:
        """Merge staged parquet data into a destination table.

        ``deduplication_method`` (``"first"``/``"last"``) and
        ``deduplication_order_by`` control deterministic batch-local
        deduplication of rows sharing ``unique_key``. Providers that cannot
        honor an explicitly requested option must reject it loudly rather than
        silently ignoring it.
        """

    def overwrite_parquet(
        self,
        *,
        table_name: str,
        data_path: str | Path,
        override_ref: str | None = None,
    ) -> dict[str, int]:
        """Overwrite a table with staged parquet data."""
        raise NotImplementedError

    def delete_rows(
        self,
        *,
        table_name: str,
        predicate: str,
        override_ref: str | None = None,
    ) -> dict[str, int]:
        """Delete rows matching a predicate expression."""
        raise NotImplementedError

    def compact(
        self,
        *,
        table_name: str,
        override_ref: str | None = None,
    ) -> dict[str, Any]:
        """Compact small files in a table."""
        raise NotImplementedError

    def list_snapshots(
        self,
        *,
        table_name: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """List recent table snapshots for time-travel queries."""
        raise NotImplementedError

    def rollback_to_snapshot(
        self,
        *,
        table_name: str,
        snapshot_id: int | str,
    ) -> dict[str, Any]:
        """Roll back a table to a previous snapshot."""
        raise NotImplementedError

    def vacuum(
        self,
        *,
        table_name: str,
        retain_hours: int = 168,
    ) -> dict[str, Any]:
        """Remove orphan files older than the retention period."""
        raise NotImplementedError


@runtime_checkable
class VersionedCatalog(Protocol):
    """Protocol for optional catalog/versioning providers.

    Providers opt in when they support explicit branch lifecycle management
    for versioned analytical storage.
    """

    def list_branches(self) -> list[Any]:
        """List known branch references."""
        ...

    def get_branch_hash(self, name: str) -> str | None:
        """Resolve the current hash for a branch."""
        ...

    def create_branch(self, name: str, from_ref: str = "main") -> str | None:
        """Create a new branch from an existing reference."""
        ...

    def merge_branch(self, source: str, target: str = "main") -> bool:
        """Merge a source branch into a target branch."""
        ...

    def delete_branch(self, name: str) -> bool:
        """Delete a branch reference."""
        ...


@runtime_checkable
class CatalogScanner(Protocol):
    """Protocol for catalog scanners used by metadata synchronization flows."""

    def scan_all_tables(self) -> dict[str, list[dict[str, Any]]]:
        """Return all discovered tables grouped by namespace."""
        ...

    def get_table_metadata(self, namespace: str, table_name: str) -> dict[str, Any] | None:
        """Return normalized metadata for one discovered table."""
        ...


@runtime_checkable
class GovernanceBackend(Protocol):
    """Protocol for governance providers (access control, masking, policies)."""

    def list_policies(self, *, table_name: str | None = None) -> list[dict[str, Any]]:
        """List access policies, optionally filtered by table."""
        ...

    def apply_policy(self, *, policy: AccessPolicy) -> None:
        """Apply an access policy to the backend."""
        ...

    def revoke_policy(self, *, policy_id: str) -> None:
        """Revoke an access policy by identifier."""
        ...

    def check_access(self, *, principal: str, table_name: str, action: str) -> bool:
        """Check whether a principal has access for an action on a table."""
        ...


@runtime_checkable
class SchemaExtractor(Protocol):
    """Protocol for extracting a NormalizedSchema from a quality provider's native schema."""

    def extract(self, native_schema: Any) -> Any:
        """Convert a native schema into a NormalizedSchema."""
        ...


@runtime_checkable
class WorkflowValidator(Protocol):
    """Provider-neutral validation for generated workflow and schema files."""

    def validate_workflow_file(self, path: Path) -> None:
        """Validate one workflow file."""
        ...

    def validate_schema_file(self, path: Path) -> None:
        """Validate one schema file."""
        ...


@runtime_checkable
class SchemaDiscoveryProvider(SchemaExtractor, Protocol):
    """Discover native schemas and convert them to normalized schemas."""

    def discover_schemas(self) -> dict[str, Any]:
        """Return native schemas keyed by class name."""
        ...


@runtime_checkable
class NamespaceResolver(Protocol):
    """Resolve an unqualified table name to a provider-default namespace."""

    def resolve_namespace(self, table_name: str) -> str:
        """Return a fully-qualified table name."""
        ...


@runtime_checkable
class WorkflowAuthoringProvider(Protocol):
    """Protocol for providers that can create workflow files in a project."""

    def create_workflow(
        self, *, project_root: Path, request: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Create workflow files for a provider-specific request."""
        ...


@runtime_checkable
class OrchestratorOperationsProvider(Protocol):
    """Protocol for providers that expose orchestrator run and asset operations."""

    async def get_run_status(self, run_id: str) -> Any:
        """Return status for one orchestrator run."""
        ...

    async def retry_run(self, run_id: str, request: Mapping[str, Any]) -> Any:
        """Validate or request retry for one orchestrator run."""
        ...

    async def cancel_run(self, run_id: str, request: Mapping[str, Any]) -> Any:
        """Request cancellation for one orchestrator run."""
        ...

    async def get_materialization_history(self, asset_key_path: str, *, limit: int = 10) -> Any:
        """Return recent materializations for one asset."""
        ...

    async def materialize_asset(self, asset_key_path: str, request: Mapping[str, Any]) -> Any:
        """Validate or request materialization for one asset."""
        ...

    async def backfill_asset(self, asset_key_path: str, request: Mapping[str, Any]) -> Any:
        """Validate or request partition backfill for one asset."""
        ...

    async def list_partitions(self, asset_key_path: str) -> Any:
        """Return partitions for one asset."""
        ...


@runtime_checkable
class MetadataCatalog(Protocol):
    """Protocol for metadata catalog providers."""

    def health_check(self) -> bool:
        """Check provider connectivity and readiness."""
        ...

    def upsert_table(self, *, namespace: str, table: Any) -> Any:
        """Create or update one table definition in the metadata catalog."""
        ...

    def publish_quality_result(self, *, event: Any) -> None:
        """Publish one quality result payload to the metadata catalog."""
        ...

    def publish_lineage_edges(self, *, edges: list[tuple[str, str]]) -> None:
        """Publish directed lineage edges to the metadata catalog."""
        ...


@runtime_checkable
class QueryEngine(Protocol):
    """Protocol for SQL query engines used by maintenance and discovery flows."""

    def execute(
        self,
        sql: str,
        params: Any = None,
        schema: str | None = None,
    ) -> Any:
        """Execute SQL and return provider-native results."""
        ...

    def preview(
        self, relation: str, *, limit: int, offset: int = 0, schema: str | None = None
    ) -> QueryPreviewResult:
        """Return one bounded, normalized page for a fully-qualified relation."""
        ...


@dataclass(frozen=True, slots=True)
class QueryPreviewResult:
    """Provider-neutral result of one bounded relation preview."""

    columns: list[str]
    column_types: list[str]
    rows: list[dict[str, Any]]
    has_more: bool


@runtime_checkable
class RefQueryCatalogManager(Protocol):
    """Provider-neutral lifecycle for query catalogs owned by one runtime ref.

    Implementations derive a deterministic catalog identity from ``ref`` and
    must reject cleanup for references they do not own.
    """

    def provision_ref_query_catalog(self, ref: str) -> str:
        """Provision and return the query catalog owned by ``ref``."""
        ...

    def drop_ref_query_catalog(self, ref: str) -> None:
        """Drop only the query catalog owned by ``ref``."""
        ...


@runtime_checkable
class MaintenanceExecutor(Protocol):
    """Provider-neutral executor for scoped table maintenance operations."""

    def for_ref(self, ref: str) -> MaintenanceExecutor:
        """Return an executor whose backend connection targets ``ref``."""
        ...

    def compact_table(
        self,
        *,
        table_name: str,
        ref: str,
        expected_revision: str | int | None = None,
        operation_id: str | None = None,
    ) -> Any:
        """Compact one table on the explicitly selected ref."""
        ...

    def expire_snapshots_table(
        self,
        *,
        table_name: str,
        ref: str,
        expected_revision: str | int | None,
        retention_hours: int,
        retain_last: int,
        operation_id: str | None = None,
    ) -> Any:
        """Expire snapshots through the provider's selected reference.

        The executor must preflight the supplied revision before submitting its
        provider-specific statement. This is an optimistic, non-atomic guard;
        it does not bind an exact deletion set or serialize other references.
        """
        ...


@runtime_checkable
class MaintenanceRetentionStore(Protocol):
    """Provider-neutral plan/execute contract for retention maintenance."""

    def expire_snapshots(
        self,
        *,
        table_name: str,
        override_ref: str | None = None,
        catalog: str | None = None,
        dry_run: bool = True,
        retention_hours: int,
        retain_last: int,
        expected_snapshot_id: int | str | None = None,
        confirmation_token: str | None = None,
        max_affected_objects: int | None = None,
        max_affected_bytes: int | None = None,
        operation_id: str | None = None,
        executor: MaintenanceExecutor | None = None,
    ) -> dict[str, object]:
        """Plan or execute guarded snapshot expiration."""
        ...

    def cleanup_orphan_files(
        self,
        *,
        table_name: str,
        override_ref: str | None = None,
        catalog: str | None = None,
        dry_run: bool = True,
        retention_hours: int,
        expected_snapshot_id: int | str | None = None,
        confirmation_token: str | None = None,
        max_affected_objects: int | None = None,
        max_affected_bytes: int | None = None,
        operation_id: str | None = None,
    ) -> dict[str, object]:
        """Plan or execute guarded orphan-file cleanup."""
        ...


@runtime_checkable
class ObjectInventoryStore(Protocol):
    """Provider-neutral evidence source for a complete owned-prefix scan."""

    def inventory_owned_prefix(
        self,
        *,
        location: str,
        retention_cutoff: datetime,
        page_size: int = 1_000,
    ) -> ObjectInventory:
        """Return a complete inventory or a failure result with no partial set."""
        ...


@runtime_checkable
class MaintenanceDiscovery(Protocol):
    """Provider-neutral catalog discovery and table-statistics contract."""

    def list_tables(self, *, namespace: str, ref: str) -> list[str]:
        """List fully qualified tables in a namespace and reference."""
        ...

    def list_namespaces(self, *, ref: str) -> list[str]:
        """List namespaces visible on a reference."""
        ...

    def get_table_stats(self, *, table_name: str, ref: str) -> dict[str, Any]:
        """Return normalized maintenance statistics for one table."""
        ...


@runtime_checkable
class MaintenanceTableStore(Protocol):
    """Provider-neutral table-store contract for planned compaction."""

    def compact(
        self,
        *,
        table_name: str,
        override_ref: str | None = None,
        dry_run: bool = False,
        expected_revision: str | int | None = None,
        operation_id: str | None = None,
        executor: MaintenanceExecutor | None = None,
    ) -> dict[str, Any]:
        """Plan or execute compaction for one table."""
        ...


@runtime_checkable
class LineageSink(Protocol):
    """Protocol for lineage backends and queryable lineage stores."""

    def record_asset_edges(
        self,
        edges: list[tuple[str, str]],
        *,
        asset_keys: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        tags: dict[str, str] | None = None,
    ) -> int:
        """Persist directed asset lineage edges."""
        ...

    def record_row_lineage(
        self,
        *,
        row_id: str,
        table_name: str,
        source_type: str,
        parent_row_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist one row-level lineage record."""
        ...

    def record_column_lineage(self, mappings: list[dict[str, Any]]) -> int:
        """Persist column-level lineage mappings."""
        ...

    def get_asset_graph(self) -> Any:
        """Return the current asset-level lineage graph representation."""
        ...

    def get_row_journey(self, *, row_id: str, depth: int = 10) -> Any:
        """Return upstream and downstream lineage for one row identifier."""
        ...


@runtime_checkable
class MaintenanceReadModel(Protocol):
    """Protocol for maintenance and observability status read models."""

    def load_maintenance_status(self) -> Any:
        """Load the current maintenance status snapshot."""
        ...

    def render_maintenance_prometheus(self) -> str:
        """Render maintenance metrics in Prometheus text format."""
        ...


@runtime_checkable
class AlertSink(Protocol):
    """Protocol for alerting providers used by orchestrators and APIs."""

    def send_alert(
        self,
        *,
        title: str,
        message: str,
        severity: str | None = None,
        asset_name: str | None = None,
        run_id: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """Send one alert notification."""
        ...


@runtime_checkable
class SchemaMigrator(Protocol):
    """Protocol for storage-layer schema migration providers.

    Each storage provider (Iceberg, Delta, Hudi) implements this protocol
    and determines its own classification rules based on its capabilities.
    """

    def supported_changes(self) -> set[str]:
        """Return the set of change_type values this provider supports natively."""
        ...

    def classify_change(self, change_type: str, **details: Any) -> str:
        """Classify a single change as 'safe', 'warning', or 'breaking'."""
        ...

    def diff_schema(self, *, table_name: str, desired: Any) -> Any:
        """Compare desired schema against current table and produce a migration plan."""
        ...

    def apply_plan(self, *, plan: Any, approved: bool = False) -> dict[str, Any]:
        """Execute a migration plan. Breaking changes require approved=True."""
        ...

    def get_schema_history(self, *, table_name: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return schema version history for a table."""
        ...


class AccessPolicy:
    """Value object describing an access control policy."""

    __slots__ = (
        "policy_id",
        "principal",
        "table_pattern",
        "action",
        "effect",
        "columns",
        "row_filter",
        "data_masking",
    )

    def __init__(
        self,
        *,
        policy_id: str | None = None,
        principal: str,
        table_pattern: str,
        action: str = "SELECT",
        effect: str = "ALLOW",
        columns: list[str] | None = None,
        row_filter: str | None = None,
        data_masking: dict[str, str] | None = None,
    ) -> None:
        """Define which principal may act on which tables, with optional masking and filters."""
        self.policy_id = policy_id
        self.principal = principal
        self.table_pattern = table_pattern
        self.action = action
        self.effect = effect
        self.columns = columns
        self.row_filter = row_filter
        self.data_masking = data_masking


@dataclass(frozen=True)
class PlatformHealthSummary:
    """Platform health summary from observability backend."""

    overall_status: str
    components: dict[str, str]
    timestamp: str


@dataclass(frozen=True)
class ServiceStatus:
    """Service status from observability backend."""

    name: str
    status: str
    last_check: str


@dataclass(frozen=True)
class PlatformMetricsSummary:
    """Platform metrics summary from observability backend."""

    period: str
    metrics: dict[str, Any]
    timestamp: str


@dataclass(frozen=True)
class AlertSummary:
    """Alert summary from observability backend."""

    title: str
    severity: str
    status: str
    fired_at: str


@dataclass(frozen=True)
class DashboardLink:
    """Dashboard link from observability backend."""

    title: str
    url: str
    category: str | None = None


@dataclass(frozen=True)
class TraceSpan:
    """Trace span row from an observability backend."""

    timestamp: str
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    span_name: str = ""
    service_name: str | None = None
    span_kind: str | None = None
    duration_ms: float | None = None
    status_code: str | None = None
    span_attributes: dict[str, Any] = field(default_factory=dict)
    resource_attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TraceSpanFilter:
    """Filter set for observability trace span queries."""

    run_id: str | None = None
    asset_key: str | None = None
    job_name: str | None = None
    service_name: str | None = None
    span_name: str | None = None
    status_code: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    limit: int = 500


@dataclass(frozen=True)
class Principal:
    """Principal attempting an action."""

    subject: str
    principal_type: str
    roles: tuple[str, ...] = ()
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResourceRef:
    """Reference to a resource being accessed."""

    resource_type: str
    resource_id: str
    tenant: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionContext:
    """Context for an authorization decision."""

    environment: str | None = None
    request_id: str | None = None
    ip_address: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AuthorizationDecision:
    """Result of an authorization evaluation."""

    allowed: bool
    reason_code: str
    policy_id: str | None = None
    explanation: str | None = None


@runtime_checkable
class AuthorizationPolicyBackend(Protocol):
    """Protocol for authorization policy decision point (PDP) providers."""

    def is_allowed(
        self,
        principal: Principal,
        action: str,
        resource: ResourceRef,
        context: DecisionContext | None = None,
    ) -> bool:
        """Check if an action is allowed."""
        ...

    def explain_decision(
        self,
        principal: Principal,
        action: str,
        resource: ResourceRef,
        context: DecisionContext | None = None,
    ) -> AuthorizationDecision:
        """Explain an authorization decision with full details."""
        ...

    def filter_resources(
        self,
        principal: Principal,
        resources: list[ResourceRef],
        action: str,
        context: DecisionContext | None = None,
    ) -> list[ResourceRef]:
        """Filter resources to only those the principal can access."""
        ...


@dataclass(frozen=True)
class AuthPrincipal:
    """Normalized caller identity from authentication."""

    subject: str
    principal_type: str  # "user" | "service" | "platform"
    issuer: str | None = None
    email: str | None = None
    groups: tuple[str, ...] = ()
    claims: dict[str, Any] = field(default_factory=dict)
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AuthenticatedSession:
    """Validated auth state associated with a caller."""

    principal: AuthPrincipal
    auth_method: str  # "oidc" | "proxy" | "bearer_token" | "session" | "static"
    provider_name: str
    session_id: str | None = None
    expires_at: datetime | None = None
    issued_at: datetime | None = None
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AuthResult:
    """Normalized result of an authentication step."""

    authenticated: bool
    principal: AuthPrincipal | None = None
    session: AuthenticatedSession | None = None
    reason_code: str | None = None


@dataclass(frozen=True)
class BrowserLoginStart:
    """Result of starting a browser-based login flow."""

    redirect_url: str
    state_token: str | None = None
    code_verifier: str | None = None


@dataclass(frozen=True)
class LogoutResult:
    """Result of a logout operation."""

    success: bool
    redirect_url: str | None = None


class RequestContext:
    """Request-scoped input presented to the authentication provider.

    This is a simple container that adapters can populate with request data
    (headers, cookies, etc.) for the authentication provider to validate.
    """

    def __init__(
        self,
        headers: Mapping[str, str] | None = None,
        cookies: Mapping[str, str] | None = None,
        query_params: Mapping[str, str] | None = None,
        method: str | None = None,
        path: str | None = None,
        remote_addr: str | None = None,
    ):
        """Copy the request mappings defensively so later mutation cannot alias callers."""
        self.headers = dict(headers) if headers else {}
        self.cookies = dict(cookies) if cookies else {}
        self.query_params = dict(query_params) if query_params else {}
        self.method = method
        self.path = path
        self.remote_addr = remote_addr


@runtime_checkable
class AuthenticationProvider(Protocol):
    """Protocol for authentication providers.

    Every provider must implement the mandatory interface.
    Optional browser flows may raise NotImplementedError if not supported.
    """

    def authenticate(self, request_context: RequestContext) -> AuthResult:
        """Authenticate a request and return the result."""
        ...

    def current_principal(self, request_context: RequestContext) -> AuthPrincipal | None:
        """Get the current principal from an already-authenticated request."""
        ...

    def validate_token(self, token: str) -> AuthenticatedSession | None:
        """Validate a bearer token and return session if valid."""
        ...

    def start_login(self) -> BrowserLoginStart:
        """Start a browser-based login flow (optional)."""
        raise NotImplementedError

    def finish_login(self, request_context: RequestContext) -> AuthResult:
        """Finish a browser-based login flow (optional)."""
        raise NotImplementedError

    def logout(self, request_context: RequestContext) -> LogoutResult:
        """Log out the current user (optional)."""
        raise NotImplementedError

    def exchange_token(self, token: str) -> AuthenticatedSession | None:
        """Exchange one token type for another (optional)."""
        raise NotImplementedError

    def authenticate_proxy_identity(self, request_context: RequestContext) -> AuthResult:
        """Authenticate reverse-proxy asserted identity (optional)."""
        raise NotImplementedError
