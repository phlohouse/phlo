"""Provider-neutral read models for the Mission Control screens.

These are the payloads the Observatory UI consumes for the nine Mission Control
pages. They are deliberately separate from ``observatory_models``: those describe
the data-platform substrate (assets, runs, tables) whereas these describe the
operator-facing read models layered on top (attention, releases, governance,
service readiness, workspace settings).

No provider URL, credential or raw backend payload may appear in these models.
"""

from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field

Severity = Literal["danger", "warning", "accent", "muted"]
Outcome = Literal["success", "warning", "danger", "accent", "muted"]


# ------------------------------------------------------------- read evidence
#
# Every Mission Control response carries typed data plus read evidence so a
# screen can distinguish live data, a preserved last-confirmed answer, an
# unavailable dependency, an unsupported panel and demo content.

EvidenceStatus = Literal["live", "stale", "unavailable", "unsupported", "demo"]

ReasonCode = Literal[
    "no_producer",
    "provider_unavailable",
    "corrupt",
    "partial",
    "absent",
    "stale",
    "demo",
]


class ReadEvidence(BaseModel):
    """How the accompanying data was produced."""

    status: EvidenceStatus
    project_id: str | None = None
    environment_id: str | None = None
    source: str
    observed_at: str
    last_confirmed_at: str | None = None
    reason_code: ReasonCode | None = None
    detail: str | None = None
    dropped_records: int = 0


DataT = TypeVar("DataT")


class ReadEnvelope(BaseModel, Generic[DataT]):
    """Typed data plus the evidence describing how it was produced."""

    data: DataT | None
    evidence: ReadEvidence


# ---------------------------------------------------------- mission context
#
# The deployment context binds the app to its one configured project and
# environment. It reports configuration truth — which dependencies are
# configured, reachable or unavailable — so the UI and operators can tell a
# healthy control plane apart from a degraded one without guessing.


class MissionDependencyStatus(BaseModel):
    """Sanitized status of one dependency the deployment relies on."""

    name: str
    kind: Literal["storage", "capability", "provider", "runtime", "security"]
    status: Literal["ready", "unavailable", "unconfigured", "unsupported"]
    detail: str | None = None


class MissionActionAvailability(BaseModel):
    """Whether one mutation family can be dispatched right now."""

    action: str
    available: bool
    reason: str | None = None


class MissionContext(BaseModel):
    """The deployment's configured project, environment and control readiness."""

    project_id: str | None
    project_root: str | None
    project_valid: bool
    project_detail: str | None = None
    environment_id: str
    data_mode: Literal["live", "demo"]
    read_ready: bool
    control_ready: bool
    blockers: list[str] = Field(default_factory=list)
    dependencies: list[MissionDependencyStatus] = Field(default_factory=list)
    actions: list[MissionActionAvailability] = Field(default_factory=list)
    observed_at: str


class MissionAlert(BaseModel):
    """One entry in the workspace alerts inbox."""

    id: str
    severity: Severity
    title: str
    detail: str
    action: str | None = None
    raised_at: str


class MissionAttentionItem(BaseModel):
    """A priority item on the Overview, ranked by consumer impact."""

    id: str
    severity: Severity
    title: str
    detail: str
    action: str
    target: str


class MissionExecutionRow(BaseModel):
    """A workflow currently running or queued."""

    id: str
    workflow: str
    stage: str
    progress: str
    elapsed: str
    run_id: str


class MissionDataProduct(BaseModel):
    """A released dataset as shown on the Overview."""

    id: str
    name: str
    freshness: str
    quality: str
    released: str
    consumers: int
    target: str


class MissionHealthService(BaseModel):
    """A service row in the Overview health rail."""

    name: str
    role: str
    state: str


class MissionQueueItem(BaseModel):
    """An entry in the Overview release queue."""

    name: str
    state: str
    detail: str


class MissionRailStat(BaseModel):
    """A label/value row in the Overview rail."""

    label: str
    value: str
    tone: Outcome = "muted"


class MissionServiceVisibility(BaseModel):
    """Operator-curated service-health visibility for the Overview rail.

    ``shown`` names the catalog services the rail may display; ``None`` means
    no configuration has been recorded, so the rail applies its default view:
    every long-running service, excluding one-shot tasks such as setup jobs.
    """

    shown: list[str] | None
    configured: bool


class MissionServiceOption(BaseModel):
    """A catalog service the operator may include in the rail.

    ``lifecycle`` is ``task`` for declared one-shot jobs (compose restart
    policy ``no``) and ``service`` for long-running processes.
    """

    name: str
    role: str
    state: str
    lifecycle: str = "service"


class MissionOverviewRail(BaseModel):
    """The Overview right-hand rail, served as one read model."""

    id: str
    ready_count: int
    total_services: int
    services: list[MissionHealthService]
    release_queue: list[MissionQueueItem]
    governance: list[MissionRailStat]
    recovery: list[MissionRailStat]
    service_options: list[MissionServiceOption] = []
    service_visibility: MissionServiceVisibility = MissionServiceVisibility(
        shown=None, configured=False
    )


class MissionEnvironment(BaseModel):
    """A deployment environment the workspace can be scoped to."""

    name: str
    state: Literal["ready", "degraded", "idle"]


# --------------------------------------------------------------------- runs


class RunStageView(BaseModel):
    """One stage of a run during its execution window."""

    run_id: str
    name: str
    provider: str
    outcome: str
    offset_percent: float = Field(ge=0, le=100)
    width_percent: float = Field(ge=0, le=100)
    duration: str
    note: str | None = None
    flagged: bool = False


class RunQualitySampleRow(BaseModel):
    """A failing row retained for inspection after a quality check failed."""

    key: str
    record_id: str
    observed_at: str
    occurrences: int


class RunQualityFailure(BaseModel):
    """The blocking quality failure for a run, with a bounded sample."""

    run_id: str
    check: str
    verdict: str
    detail: str
    sample: list[RunQualitySampleRow]
    sample_total: int


class RunCheckOutcome(BaseModel):
    """One quality result pinned to this run's attempt by the store row itself.

    `outcome` separates execution status from evaluation result — a check
    that ran and failed is not the same as a check whose execution failed.
    """

    run_id: str
    check: str
    asset: str | None = None
    stage: str | None = None
    attempt: int = 1
    outcome: str
    severity: str | None = None
    blocking: bool = False
    evaluated: int | None = None
    failed: int | None = None
    tone: Outcome = "muted"


class RunQualityReport(BaseModel):
    """All quality outcomes recorded against one run attempt."""

    run_id: str
    attempt: int | None = None
    results: list[RunCheckOutcome]
    blocking_failure: RunQualityFailure | None = None


class RunEvent(BaseModel):
    """A key execution event surfaced without needing the full log."""

    run_id: str
    at: str
    level: Literal["INFO", "ERROR"]
    message: str
    attempt: int | None = None


class RunSpan(BaseModel):
    """A trace span with its share of the run window."""

    run_id: str
    name: str
    duration: str
    width_percent: float = Field(ge=0, le=100)
    tone: Outcome


class RunArtifact(BaseModel):
    """A recorded evidence artifact."""

    run_id: str
    name: str
    size: str
    checksum: str


class RunConsumer(BaseModel):
    """A downstream reader affected by a run's outcome."""

    run_id: str
    name: str
    role: str
    current_snapshot: str | None = None


class RunConfigurationRow(BaseModel):
    """A resolved configuration entry for a run."""

    run_id: str
    label: str
    value: str


# ------------------------------------------------------------------ dataset


class DatasetOwnership(BaseModel):
    """Accountable ownership and contract summary for a dataset."""

    owner: str | None
    domain: str | None
    freshness_target: str | None
    schedule: str | None
    classification: str | None
    contract_version: str | None
    retention: str | None


class DatasetAccessGrant(BaseModel):
    """A principal's effective access to a dataset."""

    principal: str
    kind: Literal["read", "write", "service"]
    scope: str


class DatasetRunRef(BaseModel):
    """A run that produced or published this dataset."""

    run_id: str
    finished_at: str
    outcome: str
    release: str
    duration: str


class DatasetGovernance(BaseModel):
    """Everything the Dataset page needs beyond schema, preview and quality."""

    id: str
    dataset_id: str
    ownership: DatasetOwnership
    access: list[DatasetAccessGrant]
    runs: list[DatasetRunRef]
    stale: bool = False
    last_confirmed_at: str | None = None


# ----------------------------------------------------------------- releases


class ReleaseSummaryMetric(BaseModel):
    """One cell of the Releases summary band."""

    label: str
    value: str
    hint: str
    tone: Outcome = "muted"


class ReleaseCandidate(BaseModel):
    """A WAP-launched candidate awaiting promotion.

    ``id`` is the canonical candidate identity — the logical run id the WAP
    launch manifest and lifecycle report are keyed on, not the staging ref.
    """

    id: str
    dataset: str
    provider: str
    strategy: str
    readiness: str
    evidence: str
    created_at: str
    action: str
    run_id: str | None = None
    orchestrator_run_id: str | None = None
    staging_ref: str | None = None
    source_revision: str | None = None
    target_revision: str | None = None
    blockers: list[str] = Field(default_factory=list)


class CandidateSnapshotChange(BaseModel):
    """Row-level delta a candidate would apply to a table."""

    table: str
    released_snapshot: str
    candidate_snapshot: str
    row_delta: str


class CandidateEvidenceRow(BaseModel):
    """A required evidence item and its verdict."""

    name: str
    detail: str
    outcome: str
    tone: Outcome


class PublicationPlanRow(BaseModel):
    """A resolved field of the publication plan."""

    label: str
    value: str


class ReleaseCandidateDetail(BaseModel):
    """Full review payload for one candidate."""

    id: str
    candidate: ReleaseCandidate | None = None
    subtitle: str
    status: str
    revision: str
    snapshot_changes: list[CandidateSnapshotChange]
    required_evidence: list[CandidateEvidenceRow]
    publication_plan: list[PublicationPlanRow]
    run_id: str | None = None
    orchestrator_run_id: str | None = None
    staging_ref: str | None = None
    strategy: str | None = None
    source_revision: str | None = None
    target_revision: str | None = None
    blockers: list[str] = Field(default_factory=list)
    failure_detail: str | None = None


class PromotionPreviewCheck(BaseModel):
    """One read-only promotion-gate verdict."""

    name: str
    outcome: str
    detail: str
    passed: bool | None


class PromotionPreview(BaseModel):
    """Read-only evaluation of the current WAP promotion gates.

    ``digest`` binds every evaluated input — launch facts, current revisions,
    audit decision — so a stale preview cannot be replayed as confirmation.
    """

    candidate_id: str
    eligible: bool
    checks: list[PromotionPreviewCheck]
    digest: str
    strategy: str


class PromotionRequest(BaseModel):
    """Guarded promotion command payload.

    ``preview_digest`` binds the confirmation to the exact gate/revision
    evidence the operator reviewed; a mismatch means the candidate moved and
    the preview must be re-issued.
    """

    preview_digest: str | None = None
    idempotency_key: str | None = None
    project_id: str | None = None


class PromotionResult(BaseModel):
    """The truthful outcome of one promotion command.

    ``outcome`` distinguishes a fresh promotion from reconciliations
    (``already_promoted``, ``resumed``) and resumable/refused states — the
    caller must never present a non-terminal outcome as success.
    """

    outcome: str
    candidate_id: str
    blockers: list[str] = []
    preview_digest: str | None = None
    source_revision: str | None = None
    target_revision_before: str | None = None
    target_revision_after: str | None = None
    staging_ref: str | None = None
    source_deleted: bool = False
    resumed: bool = False
    failure_reason: str | None = None
    failure_detail: str | None = None
    release_revision: str | None = None


class CompletedRelease(BaseModel):
    """A release whose provider outcome is confirmed by a governed receipt."""

    id: str
    dataset: str
    provider: str
    provider_strategy: str
    reference: str
    finished_at: str
    outcome: str
    run_id: str | None = None
    orchestrator_run_id: str | None = None
    release_revision: str | None = None


# ----------------------------------------------------------------- platform


class PlatformSummaryMetric(BaseModel):
    """One cell of the Platform summary band."""

    label: str
    value: str
    hint: str
    tone: Outcome = "muted"


class PlatformService(BaseModel):
    """An enabled service with its runtime and readiness state.

    ``runtime_state`` and ``readiness_state`` are deliberately separate: a
    process can be running while its readiness probe fails. ``lifecycle`` is
    ``task`` for declared one-shot jobs (compose restart policy ``no``, e.g.
    setup containers) and ``service`` for long-running processes.
    """

    name: str
    role: str
    runtime_state: str
    readiness_state: str
    probe: str
    action: str
    attention: bool = False
    lifecycle: str = "service"


class ServiceProbeFact(BaseModel):
    """A probe-history fact for a service detail rail."""

    label: str
    value: str


class ServiceDependencyEdge(BaseModel):
    """A directed dependency between two services."""

    source: str
    target: str
    outcome: str
    tone: Outcome


class ServiceDiagnostics(BaseModel):
    """Diagnostics for one degraded service."""

    id: str
    name: str
    readiness_state: str
    summary: str
    facts: list[ServiceProbeFact]
    dependencies: list[ServiceDependencyEdge]
    dependency_note: str
    capability_checks: str
    capabilities: list[CandidateEvidenceRow]
    stale: bool = False
    last_confirmed_at: str | None = None


class CoverageRow(BaseModel):
    """A backup or maintenance coverage row."""

    name: str
    outcome: str
    tone: Outcome = "muted"


# --------------------------------------------------------------- governance


class PublicationReview(BaseModel):
    """A dataset awaiting a publication policy verdict."""

    dataset: str
    owner: str
    contract: str
    verdict: str
    reason: str
    action: str
    selected: bool = False


class AccessDriftEvidence(BaseModel):
    """One rung of the declared -> compiled -> verified ladder."""

    evidence: str
    permissions: str
    result: str
    drifted: bool = False


class AccessDrift(BaseModel):
    """Detected divergence between declared and enforced access."""

    dataset: str
    verdict: str
    subtitle: str
    evidence: list[AccessDriftEvidence]


class OwnershipGap(BaseModel):
    """A dataset missing an ownership or contract requirement."""

    dataset: str
    requirement: str
    owner: str
    action: str


class AuditEvent(BaseModel):
    """An immutable governance-relevant action record."""

    at: str
    actor: str
    action: str
    target: str
    outcome: str
    tone: Outcome = "muted"


# ---------------------------------------------------------------- workspace


class ProviderConnection(BaseModel):
    """A provider connection with its reachability state.

    Credentials are never exposed — not even their locator. ``endpoint`` is a
    provider-neutral display string and ``credential_configured`` reports only
    whether a credential is resolvable server-side.
    """

    name: str
    role: str
    endpoint: str
    state: str
    detail: str
    action: str
    degraded: bool = False
    credential_configured: bool = False


class ProviderImpact(BaseModel):
    """What a degraded provider does and does not affect."""

    id: str
    provider: str
    degraded: list[str]
    unaffected: list[str]


class NotificationRule(BaseModel):
    """A workspace notification routing rule."""

    name: str
    channel: str
    urgency: str


class WorkspaceMember(BaseModel):
    """A member of the workspace."""

    name: str
    role: str
    status: Literal["active", "invited", "suspended"]


class WorkspaceDefault(BaseModel):
    """A workspace-wide default setting."""

    name: str
    value: str


class MissionRunLogLine(BaseModel):
    """A raw log line for a run (distinct from curated run events)."""

    run_id: str
    at: str
    level: Literal["INFO", "ERROR"]
    message: str
    attempt: int | None = None


class RunEventPage(BaseModel):
    """One bounded page of a run's recorded events."""

    items: list[RunEvent]
    next_cursor: str | None = None
    total: int | None = None


class RunLogPage(BaseModel):
    """One bounded page of a run's retained log lines."""

    items: list[MissionRunLogLine]
    next_cursor: str | None = None
    total: int | None = None


class RunIdentity(BaseModel):
    """Explicit identity join for a run — logical, provider, attempt, report.

    Every field names the record it came from; ids are never merged by
    display name and never inferred from a different run's payload.
    """

    run_id: str
    durable_run_id: str | None = None
    provider_run_id: str | None = None
    attempt: int | None = None
    report: str | None = None
    pipeline: str | None = None
    operation_id: str | None = None
    launch_digest: str | None = None
    asset_ids: list[str] = Field(default_factory=list)


class SummaryMetricRow(BaseModel):
    """Generic summary-band cell, reused where a page only needs label/value."""

    label: str
    value: str
    hint: str
    tone: Outcome = "muted"


class MissionRunRow(BaseModel):
    """One row of the run list — the drilldown counterpart of the counters."""

    id: str
    name: str
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None
    asset_ids: list[str] = Field(default_factory=list)


class MissionRunList(BaseModel):
    """Bounded, cursor-paginated run list from the orchestrator population.

    This is the same population the overview execution counters are computed
    from, so the drilldown can never disagree with its summary.
    """

    items: list[MissionRunRow]
    next_cursor: str | None = None


class MissionRunDetail(BaseModel):
    """Everything the run header and details rail need beyond the evidence tabs."""

    id: str
    workflow: str
    run_id: str
    status: str
    summary: str
    metrics: list[SummaryMetricRow]
    details: list[RunConfigurationRow]
    consumers: list[RunConsumer]
    artifacts: list[RunArtifact]
    identity: RunIdentity | None = None
    failure: str | None = None


class DatasetSchemaField(BaseModel):
    """A column in a dataset schema."""

    field: str
    type: str
    nullable: str
    role: str


class LineageNode(BaseModel):
    """A node in a dataset lineage chain."""

    name: str
    role: str
    current: bool = False


class DatasetCheck(BaseModel):
    """A quality check bound to a dataset."""

    name: str
    outcome: str
    tone: Outcome


class DatasetPreview(BaseModel):
    """A bounded preview of released data."""

    columns: list[str]
    rows: list[list[str]]
    ref: str | None = None
    pinned: bool = False
    state: str = "ready"
    detail: str | None = None
    has_more: bool = False
    limit: int = 50
    offset: int = 0


class MissionDatasetDetail(BaseModel):
    """Full read model for the Dataset page, served as one payload.

    The page is tabbed with small payloads per tab; one request avoids a
    waterfall on first paint without moving significant data.
    """

    id: str
    name: str
    status: str
    summary: str
    metrics: list[SummaryMetricRow]
    schema_fields: list[DatasetSchemaField]
    preview: DatasetPreview
    checks: list[DatasetCheck]
    lineage: list[LineageNode]
    ownership: DatasetOwnership
    access: list[DatasetAccessGrant]
    runs: list[DatasetRunRef]
    runs_state: str = "ready"


class PublicationPlan(BaseModel):
    """The resolved publication plan for a dataset."""

    id: str
    dataset_id: str
    subtitle: str
    status: str
    rows: list[PublicationPlanRow]
