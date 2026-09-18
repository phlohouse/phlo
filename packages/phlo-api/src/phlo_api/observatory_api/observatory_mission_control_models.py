"""Provider-neutral read models for the Mission Control screens.

These are the payloads the Observatory UI consumes for the nine Mission Control
pages. They are deliberately separate from ``observatory_models``: those describe
the data-platform substrate (assets, runs, tables) whereas these describe the
operator-facing read models layered on top (attention, releases, governance,
service readiness, workspace settings).

No provider URL, credential or raw backend payload may appear in these models.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["danger", "warning", "accent", "muted"]
Outcome = Literal["success", "warning", "danger", "accent", "muted"]


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


class RunEvent(BaseModel):
    """A key execution event surfaced without needing the full log."""

    run_id: str
    at: str
    level: Literal["INFO", "ERROR"]
    message: str


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
    """A candidate snapshot awaiting promotion."""

    id: str
    dataset: str
    provider: str
    strategy: str
    readiness: str
    evidence: str
    created_at: str
    action: str


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


class CompletedRelease(BaseModel):
    """A release whose provider outcome is confirmed."""

    id: str
    dataset: str
    provider: str
    provider_strategy: str
    reference: str
    finished_at: str
    outcome: str


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
    process can be running while its readiness probe fails.
    """

    name: str
    role: str
    runtime_state: str
    readiness_state: str
    probe: str
    action: str
    attention: bool = False


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
