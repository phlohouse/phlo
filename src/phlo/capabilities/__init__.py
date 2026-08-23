"""Capability primitives and registry for Phlo.

This module provides the capability system that enables runtime feature discovery
and dependency injection. Capabilities abstract concrete implementations of
features like query engines, table stores, and authentication providers.

The capability system allows Phlo to:
    - Register and discover implementations at runtime
    - Resolve capabilities based on configuration and availability
    - Provide a unified interface for diverse backends
    - Support plugin-based extensibility

Key Concepts:
    - **Capability**: An abstract feature (e.g., "query_engine")
    - **Provider**: A concrete implementation (e.g., TrinoQueryEngine)
    - **Specification**: Configuration for a capability instance
    - **Registry**: Thread-safe storage for capability specifications
    - **Resolution**: Selecting the best available provider

Capability Types:
    - ``query_engine``: SQL query execution (Trino, DuckDB, etc.)
    - ``table_store``: Table storage (Iceberg, Delta, etc.)
    - ``catalog``: Metadata catalog (Nessie, OpenMetadata, etc.)
    - ``object_store``: Object storage (MinIO, S3, etc.)
    - ``authentication``: User authentication providers
    - ``authorization``: Policy-based access control
    - ``observability``: Metrics and monitoring backends
    - ``alert_sink``: Alert notification destinations
    - ``quality_backend``: Data quality validation
    - ``schema_migrator``: Schema evolution management
    - ``maintenance_read_model``: Maintenance status tracking

Main Components:
    - :class:`CapabilityRegistry`: Thread-safe capability storage
    - :func:`get_capability_registry`: Access the global registry
    - :func:`resolve_capability`: Resolve a capability to a provider
    - :func:`register_capability`: Register capability implementations
    - :class:`CapabilitySupport`: Declare operational guarantees
    - :class:`RuntimeContext`: Context for capability resolution

Example:
    ```python
    from phlo.capabilities import (
        get_capability_registry,
        resolve_capability,
        register_capability,
        QueryEngineSpec,
    )
    from phlo.capabilities.interfaces import QueryEngine

    # Register a query engine
    registry = get_capability_registry()
    register_capability(
        "query_engine",
        QueryEngineSpec(
            name="trino",
            provider=MyTrinoEngine(),
            metadata={"default_catalog": "iceberg"}
        )
    )

    # Resolve the best available query engine
    result = resolve_capability("query_engine")
    if result:
        engine: QueryEngine = result.provider
        rows = engine.execute("SELECT * FROM iceberg.my_table")
    ```

See Also:
    - :mod:`phlo.plugins.base`: Plugin base classes using capabilities
    - :mod:`phlo.capabilities.registry`: Capability registration
    - :mod:`phlo.capabilities.resolver`: Capability resolution
    - :mod:`phlo.capabilities.specs`: Capability specifications
    - :mod:`phlo.capabilities.interfaces`: Capability interfaces

Note:
    This module uses lazy loading for resolver functions to prevent
    circular imports during plugin discovery.
Aggregating package root of the capability subsystem: re-exports the interfaces,
inventory, registry, resolver, runtime, specs, and workflow-wizard modules.
"""

from typing import TYPE_CHECKING

from phlo.capabilities.interfaces import (
    AlertSink,
    AuthenticatedSession,
    AuthenticationProvider,
    AuthorizationDecision,
    AuthorizationPolicyBackend,
    AuthPrincipal,
    AuthResult,
    BrowserLoginStart,
    CatalogScanner,
    DecisionContext,
    LogoutResult,
    MaintenanceDiscovery,
    MaintenanceExecutor,
    MaintenanceReadModel,
    MaintenanceRetentionStore,
    MaintenanceTableStore,
    NamespaceResolver,
    ObjectInventoryStore,
    OrchestratorOperationsProvider,
    Principal,
    QueryEngine,
    QueryPreviewResult,
    RefQueryCatalogManager,
    RequestContext,
    ResourceRef,
    SchemaDiscoveryProvider,
    SchemaExtractor,
    SchemaMigrator,
    TableStateObservation,
    TableStateObserver,
    TableStore,
    TraceSpan,
    TraceSpanFilter,
    WorkflowAuthoringProvider,
    WorkflowValidator,
)
from phlo.capabilities.inventory import InventoryObject, ObjectInventory
from phlo.capabilities.maintenance import (
    SAFE_MIN_RETENTION_HOURS,
    DefaultMaintenanceReadModel,
    MaintenanceExecutionError,
    MaintenanceExecutionPhase,
    MaintenanceOperationResult,
    MaintenanceOperationState,
    MaintenanceOperationStatus,
    MaintenancePreconditionError,
    MaintenanceStatusSnapshot,
    load_maintenance_status,
    render_maintenance_prometheus,
)
from phlo.capabilities.observability import (
    DefaultObservabilityBackend,
    register_default_capability_providers,
)
from phlo.capabilities.registry import (
    CapabilityRegistry,
    clear_all_capabilities,
    clear_capabilities,
    get_capability_registry,
    register_capability,
)
from phlo.capabilities.runtime import (
    RuntimeContext,
    RuntimeRouting,
    resolve_runtime_ref,
    routing_from_context,
)
from phlo.capabilities.specs import (
    AlertSinkSpec,
    ApiBackendSpec,
    AssetCheckSpec,
    AssetSpec,
    AuthenticationProviderSpec,
    AuthorizationPolicyBackendSpec,
    CatalogScannerSpec,
    CatalogSpec,
    CheckResult,
    DataMigrationSourceSpec,
    FieldSpec,
    GovernanceBackendSpec,
    LineageSinkSpec,
    MaintenanceExecutorSpec,
    MaintenanceReadModelSpec,
    MaterializeResult,
    MetadataCatalogSpec,
    NamespaceResolverSpec,
    NormalizedSchema,
    ObjectStoreSpec,
    ObservabilityBackendSpec,
    OrchestratorOperationsSpec,
    PartitionSpec,
    PublishTargetSpec,
    QualityBackendSpec,
    QueryEngineSpec,
    RegulatedSurfaceSpec,
    ResourceSpec,
    RunResult,
    RunSpec,
    SchemaChange,
    SchemaDiscoverySpec,
    SchemaMigrationPlan,
    SchemaMigrationSpec,
    SettingsStoreSpec,
    TableStoreSpec,
    WorkflowAuthoringSpec,
    WorkflowValidationSpec,
)
from phlo.capabilities.support import CapabilitySupport, coerce_capability_support
from phlo.capabilities.telemetry import TelemetryRecorder, get_telemetry_path, iter_telemetry_events
from phlo.capabilities.workflow_wizard import (
    WorkflowApplyAction,
    WorkflowContributionMode,
    WorkflowFilePreview,
    WorkflowProposal,
    WorkflowProposalRequest,
    WorkflowStageSelection,
    WorkflowWizardContribution,
    WorkflowWizardField,
    detect_file_conflicts,
    validate_proposal_request,
)

if TYPE_CHECKING:
    from phlo.capabilities.resolver import ResolutionResult

__all__ = [
    "AlertSink",
    "AlertSinkSpec",
    "ApiBackendSpec",
    "AssetCheckSpec",
    "AssetSpec",
    "AuthenticatedSession",
    "AuthPrincipal",
    "AuthResult",
    "AuthenticationProvider",
    "AuthenticationProviderSpec",
    "AuthorizationDecision",
    "AuthorizationPolicyBackend",
    "AuthorizationPolicyBackendSpec",
    "BrowserLoginStart",
    "CatalogSpec",
    "CatalogScanner",
    "CatalogScannerSpec",
    "CapabilitySupport",
    "CapabilityRegistry",
    "CheckResult",
    "DataMigrationSourceSpec",
    "DecisionContext",
    "DefaultMaintenanceReadModel",
    "MaintenanceOperationResult",
    "MaintenanceOperationState",
    "MaintenancePreconditionError",
    "MaintenanceExecutionError",
    "MaintenanceExecutionPhase",
    "MaintenanceExecutor",
    "MaintenanceDiscovery",
    "MaintenanceRetentionStore",
    "ObjectInventoryStore",
    "InventoryObject",
    "ObjectInventory",
    "MaintenanceTableStore",
    "MaintenanceExecutorSpec",
    "DefaultObservabilityBackend",
    "FieldSpec",
    "get_telemetry_path",
    "GovernanceBackendSpec",
    "LineageSinkSpec",
    "LogoutResult",
    "MaintenanceReadModel",
    "MaintenanceOperationStatus",
    "MaintenanceReadModelSpec",
    "MaintenanceStatusSnapshot",
    "SAFE_MIN_RETENTION_HOURS",
    "MaterializeResult",
    "MetadataCatalogSpec",
    "NamespaceResolver",
    "NamespaceResolverSpec",
    "NormalizedSchema",
    "ObjectStoreSpec",
    "ObservabilityBackendSpec",
    "OrchestratorOperationsProvider",
    "OrchestratorOperationsSpec",
    "PartitionSpec",
    "Principal",
    "TraceSpan",
    "TraceSpanFilter",
    "PublishTargetSpec",
    "QueryEngine",
    "QueryPreviewResult",
    "RefQueryCatalogManager",
    "QualityBackendSpec",
    "QueryEngineSpec",
    "RegulatedSurfaceSpec",
    "RequestContext",
    "ResourceRef",
    "ResourceSpec",
    "RunResult",
    "RunSpec",
    "RuntimeContext",
    "RuntimeRouting",
    "WorkflowApplyAction",
    "WorkflowAuthoringProvider",
    "WorkflowAuthoringSpec",
    "WorkflowValidationSpec",
    "WorkflowValidator",
    "WorkflowContributionMode",
    "WorkflowFilePreview",
    "WorkflowProposal",
    "WorkflowProposalRequest",
    "WorkflowStageSelection",
    "WorkflowWizardContribution",
    "WorkflowWizardField",
    "SchemaChange",
    "SchemaDiscoveryProvider",
    "SchemaDiscoverySpec",
    "SchemaExtractor",
    "SchemaMigrationPlan",
    "SchemaMigrationSpec",
    "SchemaMigrator",
    "SettingsStoreSpec",
    "TableStoreSpec",
    "TableStore",
    "TableStateObservation",
    "TableStateObserver",
    "coerce_capability_support",
    "clear_all_capabilities",
    "clear_capabilities",
    "configured_capability_name",
    "detect_file_conflicts",
    "get_capability_registry",
    "list_capabilities",
    "missing_required_capabilities",
    "register_capability",
    "register_default_capability_providers",
    "ResolutionResult",
    "render_maintenance_prometheus",
    "resolve_runtime_ref",
    "resolve_capability",
    "routing_from_context",
    "TelemetryRecorder",
    "validate_proposal_request",
    "iter_telemetry_events",
    "load_maintenance_status",
]


def __getattr__(name: str):
    """Lazily expose resolver symbols to avoid circular imports."""
    if name in {
        "ResolutionResult",
        "configured_capability_name",
        "list_capabilities",
        "missing_required_capabilities",
        "resolve_capability",
    }:
        from phlo.capabilities import resolver

        return getattr(resolver, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
