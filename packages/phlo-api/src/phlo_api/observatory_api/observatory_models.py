"""Provider-neutral Pydantic models for the Observatory API.

The Literal aliases here define the canonical status and state vocabularies;
every provider backend maps its native states onto these models so
Observatory renders one uniform surface.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

HealthState = Literal["ok", "warning", "error", "unknown"]
ControlStatus = Literal["pass", "fail", "warning", "unknown", "not_applicable"]
ServiceStatus = Literal["running", "stopped", "unhealthy", "starting", "unknown"]
ServiceDefinitionState = Literal["configured", "available"]
OperationStatus = Literal["queued", "running", "succeeded", "failed", "skipped", "unknown"]
RunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "unknown"]
QualityStatus = Literal["passing", "failing", "warning", "unknown"]
PublicationState = Literal["draft", "published", "retired"]
TelemetryIdentityDetail = Literal["anonymous", "aggregate", "identity", "audit_only"]


class ObservatoryHealth(BaseModel):
    """Neutral health state for any Observatory resource."""

    state: HealthState
    message: str | None = None


class ObservatoryExternalLink(BaseModel):
    """Provider-neutral link exposed to Observatory."""

    label: str
    url: str
    kind: str = "external"


class ObservatoryCapabilityPage(BaseModel):
    """Provider-neutral Observatory page availability."""

    id: str
    label: str
    path: str
    available: bool
    nav: bool = True
    reason: str | None = None
    providers: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatoryCapabilities(BaseModel):
    """Provider-neutral Observatory surface capability contract."""

    version: int = 2
    pages: list[ObservatoryCapabilityPage] = Field(default_factory=list)
    features: dict[str, bool] = Field(default_factory=dict)
    providers: dict[str, list[str]] = Field(default_factory=dict)


class ObservatoryCapabilitySupport(BaseModel):
    """Provider support flags exposed to Observatory."""

    supports_refs: bool = False
    supports_snapshots: bool = False
    supports_schema_evolution: bool = False
    supports_atomic_validation: bool = False
    supports_promote: bool = False
    supports_time_travel: bool = False
    supports_metrics: bool = False
    supports_logs: bool = False
    supports_dashboards: bool = False
    supports_alerts: bool = False
    supports_permissions: bool = False
    supports_attributes: bool = False


class ObservatoryCapabilityProvider(BaseModel):
    """One installed provider for a Phlo capability type."""

    capability_type: str
    name: str
    display_name: str
    package: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    support: ObservatoryCapabilitySupport = Field(default_factory=ObservatoryCapabilitySupport)
    health: ObservatoryHealth = Field(default_factory=lambda: ObservatoryHealth(state="unknown"))
    native_links: list[ObservatoryExternalLink] = Field(default_factory=list)


class ObservatoryUiContribution(BaseModel):
    """Provider-neutral UI contribution exposed by a Phlo capability."""

    name: str
    capability_type: str
    capability_name: str
    surfaces: list[str]
    read_models: dict[str, str]
    actions: list[str]
    native_links: list[ObservatoryExternalLink] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatoryRouteRequirement(BaseModel):
    """Capability requirements for one Observatory route."""

    route_id: str
    label: str
    path: str
    required_any: list[str] = Field(default_factory=list)
    required_all: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)
    nav: bool = True
    reason: str = ""


class ObservatoryCapabilityInventory(BaseModel):
    """Full capability inventory used to drive Observatory navigation."""

    version: int = 2
    providers: dict[str, list[ObservatoryCapabilityProvider]] = Field(default_factory=dict)
    requirements: list[ObservatoryRouteRequirement] = Field(default_factory=list)
    ui_contributions: list[ObservatoryUiContribution] = Field(default_factory=list)


class ObservatoryResourceRef(BaseModel):
    """Small reference to another Observatory resource."""

    kind: str
    id: str
    label: str


class ObservatorySurfaceItem(BaseModel):
    """Provider-neutral top-level surface summary."""

    id: str
    name: str
    kind: str
    health: ObservatoryHealth = Field(default_factory=lambda: ObservatoryHealth(state="unknown"))
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatoryAction(BaseModel):
    """Provider-neutral action descriptor."""

    id: str
    label: str
    kind: str
    enabled: bool = False
    requires_confirmation: bool = True
    reason: str | None = None
    risk_level: Literal["low", "medium", "high", "critical"] = "low"
    required_capability: str | None = None
    required_service: str | None = None
    required_permission: str | None = None
    equivalent_cli_command: str | None = None
    expected_evidence: list[str] = Field(default_factory=list)
    background_operation_id: str | None = None


class ObservatoryActionRequest(BaseModel):
    """Request to execute a guarded Observatory action."""

    action_id: str
    expected_state: str | None = None
    """Exact observed current state for Dataset workflow transitions.

    Compare-and-set guard: the client repeats back the state it
    saw when it explained the transition; a moved state conflicts instead of
    applying. Ignored by non-Dataset actions.
    """


class ObservatoryActionResult(BaseModel):
    """Provider-neutral result of a guarded Observatory action."""

    action: ObservatoryAction
    status: Literal["succeeded", "failed", "skipped"]
    message: str
    operation: "ObservatoryOperation | None" = None


class ObservatoryServicePort(BaseModel):
    """Provider-neutral service port exposure."""

    name: str
    target: str
    published: str | None = None


class ObservatoryServiceConfigEntry(BaseModel):
    """Non-secret service configuration hint."""

    name: str
    value: str | None = None
    description: str | None = None
    secret: bool = False


class ObservatoryService(BaseModel):
    """Provider-neutral service summary."""

    id: str
    name: str
    kind: str
    status: ServiceStatus
    health: ObservatoryHealth
    definition_state: ServiceDefinitionState = "available"
    runtime_state: ServiceStatus = "unknown"
    in_stack: bool = False
    disabled: bool = False
    profile: str | None = None
    backend: str = "unknown"
    depends_on: list[str] = Field(default_factory=list)
    impacts: list[str] = Field(default_factory=list)
    links: list[ObservatoryExternalLink] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatoryServiceDetail(BaseModel):
    """Provider-neutral service detail."""

    service: ObservatoryService
    dependencies: list[ObservatoryService] = Field(default_factory=list)
    dependents: list[ObservatoryService] = Field(default_factory=list)
    actions: list[ObservatoryAction] = Field(default_factory=list)
    logs: list["ObservatoryLogEvent"] = Field(default_factory=list)
    ports: list[ObservatoryServicePort] = Field(default_factory=list)
    config: list[ObservatoryServiceConfigEntry] = Field(default_factory=list)


class ObservatoryOverviewRow(BaseModel):
    """One canonical row for Home attention and event surfaces."""

    id: str
    kind: Literal["service", "quality", "operation", "log"]
    label: str
    href: str
    state: HealthState
    meta: str | None = None
    reason: str | None = None


class ObservatoryOverview(BaseModel):
    """First slice of the Observatory overview payload."""

    health: ObservatoryHealth
    counters: dict[str, int] = Field(default_factory=dict)
    attention: list[ObservatoryOverviewRow] = Field(default_factory=list)
    events: list[ObservatoryOverviewRow] = Field(default_factory=list)
    recent: list[ObservatoryResourceRef] = Field(default_factory=list)


class ObservatoryOperation(BaseModel):
    """Provider-neutral operation summary."""

    id: str
    name: str
    kind: str
    status: OperationStatus
    health: ObservatoryHealth
    target: ObservatoryResourceRef | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatoryOperationDetail(BaseModel):
    """Provider-neutral operation detail."""

    operation: ObservatoryOperation
    related: list[ObservatoryResourceRef] = Field(default_factory=list)
    logs: list["ObservatoryLogEvent"] = Field(default_factory=list)
    actions: list[ObservatoryAction] = Field(default_factory=list)


class ObservatoryRunReportIdentity(BaseModel):
    """Canonical durable run-report identity, populated only from complete evidence.

    The three values are the exact identity accepted by the authenticated
    run-report endpoint. They are never inferred from display names, generic
    run IDs, selected projects, operation IDs, or legacy/recovered rows.
    """

    project_id: str
    run_id: str
    attempt: int = Field(..., ge=1)


class ObservatoryRun(BaseModel):
    """Provider-neutral orchestrator run summary."""

    id: str
    name: str
    status: RunStatus = "unknown"
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None
    assets: list[ObservatoryResourceRef] = Field(default_factory=list)
    checks: list[ObservatoryResourceRef] = Field(default_factory=list)
    logs: list[ObservatoryResourceRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    report_identity: ObservatoryRunReportIdentity | None = None


class ObservatoryAsset(BaseModel):
    """Provider-neutral asset summary."""

    id: str
    name: str
    group: str | None = None
    description: str | None = None
    kinds: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatoryAssetDetail(BaseModel):
    """Provider-neutral asset detail with related operational context."""

    asset: ObservatoryAsset
    upstream: list[ObservatoryAsset] = Field(default_factory=list)
    downstream: list[ObservatoryAsset] = Field(default_factory=list)
    tables: list["ObservatoryTable"] = Field(default_factory=list)
    quality: list["ObservatoryQualityCheck"] = Field(default_factory=list)
    logs: list["ObservatoryLogEvent"] = Field(default_factory=list)
    operations: list["ObservatoryOperation"] = Field(default_factory=list)
    lineage: list[ObservatoryResourceRef] = Field(default_factory=list)
    materializations: list["ObservatoryOperation"] = Field(default_factory=list)
    column_lineage: dict[str, list[str]] = Field(default_factory=dict)


class ObservatoryDataset(BaseModel):
    """Provider-neutral Dataset summary."""

    id: str
    name: str
    description: str | None = None
    owner: str | None = None
    classifications: list[str] = Field(default_factory=list)
    publication_state: PublicationState = "draft"
    readiness_state: HealthState = "unknown"
    candidate: bool = False
    kinds: list[str] = Field(default_factory=list)
    source_refs: list[ObservatoryResourceRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatoryControlEvidence(BaseModel):
    """Evidence supporting one Dataset control."""

    kind: str
    id: str
    label: str
    value: str | None = None
    resource: ObservatoryResourceRef | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatoryDatasetControl(BaseModel):
    """One governance control evaluated against a Dataset."""

    id: str
    label: str
    status: ControlStatus
    message: str | None = None
    evidence: list[ObservatoryControlEvidence] = Field(default_factory=list)


class ObservatoryGovernanceRow(BaseModel):
    """Control matrix row for one Dataset."""

    dataset: ObservatoryDataset
    owner: str | None = None
    classifications: list[str] = Field(default_factory=list)
    status: ControlStatus = "unknown"
    controls: list[ObservatoryDatasetControl] = Field(default_factory=list)


class ObservatoryGovernanceMatrix(BaseModel):
    """Governance control matrix over Datasets."""

    controls: list[str] = Field(default_factory=list)
    rows: list[ObservatoryGovernanceRow] = Field(default_factory=list)
    status_counts: dict[str, int] = Field(default_factory=dict)


class ObservatoryTelemetryPrivacyPolicy(BaseModel):
    """Privacy shaping policy applied to Usage before UI display."""

    identity_detail: TelemetryIdentityDetail = "aggregate"
    retention_days: int | None = None
    audit_drilldown: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatoryAccessActivity(BaseModel):
    """Privacy-shaped access activity for a Dataset."""

    id: str
    action: str
    actor_label: str | None = None
    actor_kind: str | None = None
    count: int = 1
    last_seen_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatoryDependencyActivity(BaseModel):
    """Observed usage dependency involving a Dataset."""

    id: str
    source: ObservatoryResourceRef
    target: ObservatoryResourceRef
    kind: str = "dependency"
    count: int = 1
    last_seen_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatoryConsumerAdoption(BaseModel):
    """Declared consumer reliance on a Dataset."""

    id: str
    consumer: str
    kind: str = "team"
    owner: str | None = None
    status: str = "declared"
    declared_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatoryDatasetUsage(BaseModel):
    """Usage read model for one Dataset."""

    privacy_policy: ObservatoryTelemetryPrivacyPolicy = Field(
        default_factory=ObservatoryTelemetryPrivacyPolicy
    )
    access_activity: list[ObservatoryAccessActivity] = Field(default_factory=list)
    dependency_activity: list[ObservatoryDependencyActivity] = Field(default_factory=list)
    consumer_adoption: list[ObservatoryConsumerAdoption] = Field(default_factory=list)


class ObservatoryPublishingAction(BaseModel):
    """Display-only publishing action availability."""

    id: str
    label: str
    enabled: bool
    reason: str | None = None
    consequences: list[str] = Field(default_factory=list)


class ObservatoryPublishingReadiness(BaseModel):
    """Readiness policy evaluation for internal Dataset publishing."""

    state: HealthState = "unknown"
    policy_name: str = "default"
    internal_only: bool = True
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    actions: list[ObservatoryPublishingAction] = Field(default_factory=list)


class ObservatoryPipelineStage(BaseModel):
    """One stage in a Dataset production flow."""

    id: str
    label: str
    state: HealthState = "unknown"
    resource: ObservatoryResourceRef | None = None


class ObservatoryDatasetPipeline(BaseModel):
    """Production-flow read model for one Dataset."""

    dataset: ObservatoryDataset | None = None
    freshness_state: HealthState = "unknown"
    freshness_at: str | None = None
    last_run: ObservatoryResourceRef | None = None
    stages: list[ObservatoryPipelineStage] = Field(default_factory=list)
    actions: list["ObservatoryAction"] = Field(default_factory=list)


class ObservatoryPipelineList(BaseModel):
    """Production-flow summaries for Datasets."""

    items: list[ObservatoryDatasetPipeline] = Field(default_factory=list)


class ObservatoryDatasetProfile(BaseModel):
    """Shared cross-feature profile for one Dataset."""

    dataset: ObservatoryDataset
    asset: ObservatoryAsset | None = None
    tables: list["ObservatoryTable"] = Field(default_factory=list)
    quality: list["ObservatoryQualityCheck"] = Field(default_factory=list)
    upstream: list[ObservatoryResourceRef] = Field(default_factory=list)
    downstream: list[ObservatoryResourceRef] = Field(default_factory=list)
    logs: list["ObservatoryLogEvent"] = Field(default_factory=list)
    operations: list["ObservatoryOperation"] = Field(default_factory=list)
    governance: list[ObservatoryDatasetControl] = Field(default_factory=list)
    usage: ObservatoryDatasetUsage = Field(default_factory=ObservatoryDatasetUsage)
    publishing: ObservatoryPublishingReadiness = Field(
        default_factory=ObservatoryPublishingReadiness
    )
    pipeline: ObservatoryDatasetPipeline = Field(default_factory=ObservatoryDatasetPipeline)
    canonical: dict[str, Any] | None = None
    """Canonical Dataset projection; identical to `phlo dataset show --json`."""
    sections: dict[str, bool] = Field(default_factory=dict)


class ObservatoryPublishingReadinessItem(BaseModel):
    """Publishing readiness for one Dataset, keyed for list consumers."""

    dataset_id: str
    publishing: ObservatoryPublishingReadiness


class ObservatoryPublishingReadinessList(BaseModel):
    """Bounded provider-neutral publishing readiness response."""

    items: list[ObservatoryPublishingReadinessItem] = Field(default_factory=list)
    next_cursor: str | None = None


class ObservatoryAssetGraphNode(BaseModel):
    """Provider-neutral asset graph node."""

    id: str
    key: list[str] = Field(default_factory=list)
    key_path: str
    label: str
    description: str | None = None
    compute_kind: str | None = None
    group_name: str | None = None
    layer: str = "unknown"
    last_materialization: str | None = None
    upstream_count: int = 0
    downstream_count: int = 0


class ObservatoryAssetGraphEdge(BaseModel):
    """Provider-neutral asset graph edge."""

    source: str
    target: str


class ObservatoryAssetGraph(BaseModel):
    """Provider-neutral asset graph."""

    nodes: list[ObservatoryAssetGraphNode] = Field(default_factory=list)
    edges: list[ObservatoryAssetGraphEdge] = Field(default_factory=list)


class ObservatoryImpactedAsset(BaseModel):
    """Provider-neutral downstream impact summary."""

    key_path: str
    label: str
    layer: str = "unknown"
    depth: int


class ObservatoryTable(BaseModel):
    """Provider-neutral table summary."""

    id: str
    name: str
    namespace: str | None = None
    asset_id: str | None = None
    format: str | None = None
    branch: str | None = None
    schema_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatoryTablePreview(BaseModel):
    """Provider-neutral table preview metadata.

    Rows are optional because phlo-api v2 must not couple Observatory to a
    specific query engine. Implementations can populate rows when a core
    provider-neutral query contract exists.
    """

    table: ObservatoryTable
    columns: list[str] = Field(default_factory=list)
    column_types: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int | None = None
    limit: int = 50
    offset: int = 0
    has_more: bool = False
    state: str = "ready"
    message: str | None = None


class ObservatoryQueryRequest(BaseModel):
    """Provider-neutral read query request."""

    sql: str
    branch: str | None = None
    limit: int = 100
    offset: int = 0


class ObservatoryQueryResult(BaseModel):
    """Provider-neutral read query result."""

    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int | None = None
    effective_sql: str
    limit: int
    offset: int = 0
    warnings: list[str] = Field(default_factory=list)


class ObservatorySavedQuery(BaseModel):
    """Persisted Observatory query."""

    id: str
    name: str
    sql: str
    branch: str | None = None
    created_at: str
    updated_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatorySavedQueryRequest(BaseModel):
    """Create or update a saved query."""

    name: str
    sql: str
    branch: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatoryStageDiff(BaseModel):
    """Provider-neutral table stage diff."""

    source: "ObservatoryTable"
    target: "ObservatoryTable"
    columns: dict[str, list[str]] = Field(default_factory=dict)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatoryRowJourney(BaseModel):
    """Provider-neutral row journey and provenance context."""

    table: "ObservatoryTable"
    row_id: str
    row: dict[str, Any] = Field(default_factory=dict)
    upstream: list[ObservatoryResourceRef] = Field(default_factory=list)
    downstream: list[ObservatoryResourceRef] = Field(default_factory=list)
    stages: list[ObservatoryResourceRef] = Field(default_factory=list)
    logs: list["ObservatoryLogEvent"] = Field(default_factory=list)
    diff: dict[str, Any] = Field(default_factory=dict)


class ObservatoryUpstreamTableRef(BaseModel):
    """Resolved upstream table identifier for row provenance."""

    model_config = {"populate_by_name": True}

    schema_name: str = Field(alias="schema")
    table: str


class ObservatoryContributingRowsQueryRequest(BaseModel):
    """Request for a contributing-rows query."""

    model_config = {"extra": "forbid"}

    downstream_asset_key: str
    upstream_asset_key: str
    row_data: dict[str, Any]
    limit: int | None = None
    timeout_ms: int | None = None


class ObservatoryContributingRowsQueryResponse(BaseModel):
    """Generated contributing-rows query."""

    query: str
    upstream: ObservatoryUpstreamTableRef


class ObservatoryContributingRowsPageRequest(BaseModel):
    """Request for a page of contributing rows."""

    model_config = {"extra": "forbid"}

    downstream_asset_key: str
    upstream_asset_key: str
    row_data: dict[str, Any]
    page: int | None = None
    page_size: int | None = None
    timeout_ms: int | None = None


class ObservatoryContributingRowsPageResponse(BaseModel):
    """Page of contributing rows."""

    mode: Literal["entity", "aggregate"]
    page: int
    page_size: int
    has_more: bool
    query: str
    upstream: ObservatoryUpstreamTableRef
    columns: list[str] = Field(default_factory=list)
    column_types: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)


class ObservatoryQualityCheck(BaseModel):
    """Provider-neutral quality check summary."""

    id: str
    name: str
    asset_id: str
    status: QualityStatus
    severity: str | None = None
    blocking: bool = True
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatoryQualityDetail(BaseModel):
    """Provider-neutral quality check detail."""

    check: ObservatoryQualityCheck
    asset: ObservatoryAsset | None = None
    history: list[ObservatoryOperation] = Field(default_factory=list)
    logs: list[ObservatoryLogEvent] = Field(default_factory=list)
    actions: list[ObservatoryAction] = Field(default_factory=list)


class ObservatoryLogEvent(BaseModel):
    """Provider-neutral log event summary."""

    id: str
    timestamp: str | None = None
    level: str = "info"
    message: str
    source: str | None = None
    resource: ObservatoryResourceRef | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatoryLogFacets(BaseModel):
    """Provider-neutral log filter facets."""

    sources: list[str] = Field(default_factory=list)
    levels: list[str] = Field(default_factory=list)
    resources: list[ObservatoryResourceRef] = Field(default_factory=list)


class ObservatoryBranch(BaseModel):
    """Provider-neutral data branch summary."""

    id: str
    name: str
    current: bool = False
    protected: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatoryBranchDetail(BaseModel):
    """Provider-neutral branch detail."""

    branch: ObservatoryBranch
    contents: list[ObservatoryResourceRef] = Field(default_factory=list)
    commits: list[ObservatoryOperation] = Field(default_factory=list)
    compare: dict[str, int] = Field(default_factory=dict)
    tables: list["ObservatoryTable"] = Field(default_factory=list)


class ObservatoryExtension(BaseModel):
    """Provider-neutral Observatory extension summary."""

    id: str
    name: str
    version: str | None = None
    enabled: bool = True
    routes: list[str] = Field(default_factory=list)
    nav: list[str] = Field(default_factory=list)
    settings_scope: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatoryExtensionDetail(BaseModel):
    """Provider-neutral extension detail."""

    extension: ObservatoryExtension
    routes: list[str] = Field(default_factory=list)
    nav: list[str] = Field(default_factory=list)
    capabilities: list[ObservatoryResourceRef] = Field(default_factory=list)


class ObservatorySettings(BaseModel):
    """Provider-neutral Observatory settings summary."""

    version: int = 2
    defaults: dict[str, str] = Field(default_factory=dict)
    features: dict[str, bool] = Field(default_factory=dict)
    storage: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatorySearchResult(BaseModel):
    """Provider-neutral Observatory search result."""

    id: str
    label: str
    kind: str
    summary: str | None = None
    href: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservatoryServiceList(BaseModel):
    """List envelope for v2 services."""

    items: list[ObservatoryService]


class ObservatoryOperationList(BaseModel):
    """List envelope for v2 operations."""

    items: list[ObservatoryOperation]


class ObservatoryRunList(BaseModel):
    """List envelope for v2 orchestrator runs."""

    items: list[ObservatoryRun]
    next_cursor: str | None = None


class ObservatoryAssetList(BaseModel):
    """List envelope for v2 assets."""

    items: list[ObservatoryAsset]
    next_cursor: str | None = None


class ObservatoryDatasetList(BaseModel):
    """List envelope for v2 Datasets."""

    items: list[ObservatoryDataset]
    next_cursor: str | None = None


class ObservatoryDatasetFacets(BaseModel):
    """Filterable facet values across the full Dataset collection."""

    owners: list[str] = Field(default_factory=list)
    classifications: list[str] = Field(default_factory=list)
    publication_states: list[str] = Field(default_factory=list)
    readiness_states: list[str] = Field(default_factory=list)
    candidate_states: list[bool] = Field(default_factory=list)


class ObservatoryTableList(BaseModel):
    """List envelope for v2 tables."""

    items: list[ObservatoryTable]


class ObservatoryQualityList(BaseModel):
    """List envelope for v2 quality checks."""

    items: list[ObservatoryQualityCheck]


class ObservatoryLogList(BaseModel):
    """List envelope for v2 log events."""

    items: list[ObservatoryLogEvent]


class ObservatoryBranchList(BaseModel):
    """List envelope for v2 branches."""

    items: list[ObservatoryBranch]


class ObservatoryExtensionList(BaseModel):
    """List envelope for v2 extensions."""

    items: list[ObservatoryExtension]


class ObservatorySearchList(BaseModel):
    """List envelope for v2 search results."""

    items: list[ObservatorySearchResult]
    next_cursor: str | None = None


class ObservatorySavedQueryList(BaseModel):
    """List envelope for saved Observatory queries."""

    items: list[ObservatorySavedQuery]


class ObservatorySurfaceList(BaseModel):
    """List envelope for provider-neutral top-level surfaces."""

    items: list[ObservatorySurfaceItem]
