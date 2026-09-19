/**
 * TypeScript mirrors of the Mission Control Pydantic models.
 *
 * Kept hand-written and in the same field order as
 * `phlo_api/observatory_api/observatory_mission_control_models.py` so drift is
 * easy to spot in review. If these grow much further, generate them from the
 * OpenAPI schema instead.
 */

export type Severity = "danger" | "warning" | "accent" | "muted";
export type Outcome = "success" | "warning" | "danger" | "accent" | "muted";

/* --------------------------------------------------------------- evidence */

/** Provenance attached to every Mission Control read model response. */
export type EvidenceStatus = "live" | "stale" | "unavailable" | "unsupported" | "demo";
export type ReasonCode =
  | "no_producer"
  | "provider_unavailable"
  | "corrupt"
  | "partial"
  | "absent"
  | "stale"
  | "demo";

export interface ReadEvidence {
  status: EvidenceStatus;
  project_id: string | null;
  environment_id: string | null;
  source: string;
  observed_at: string;
  last_confirmed_at: string | null;
  reason_code: ReasonCode | null;
  detail: string | null;
  dropped_records: number;
}

/** Every Mission Control GET answers this envelope: payload plus evidence. */
export interface ReadEnvelope<T> {
  data: T | null;
  evidence: ReadEvidence;
}

/* -------------------------------------------------------- mission context */

export type DependencyStatus = "ready" | "unavailable" | "unconfigured" | "unsupported";

export interface MissionDependencyStatus {
  name: string;
  kind: "storage" | "capability" | "provider" | "runtime" | "security";
  status: DependencyStatus;
  detail: string | null;
}

export interface MissionActionAvailability {
  action: string;
  available: boolean;
  reason: string | null;
}

/** The deployment's one configured project, environment and control readiness. */
export interface MissionContext {
  project_id: string | null;
  project_root: string | null;
  project_valid: boolean;
  project_detail: string | null;
  environment_id: string;
  data_mode: "live" | "demo";
  read_ready: boolean;
  control_ready: boolean;
  blockers: Array<string>;
  dependencies: Array<MissionDependencyStatus>;
  actions: Array<MissionActionAvailability>;
  observed_at: string;
}

/* ------------------------------------------------------------------ shell */

export interface MissionAlert {
  id: string;
  severity: Severity;
  title: string;
  detail: string;
  action: string | null;
  raised_at: string;
}

export interface MissionAttentionItem {
  id: string;
  severity: Severity;
  title: string;
  detail: string;
  action: string;
  /** Deep link the UI resolves to a route. */
  target: string;
}

export interface MissionExecutionRow {
  id: string;
  workflow: string;
  stage: string;
  progress: string;
  elapsed: string;
  run_id: string;
}

export interface MissionDataProduct {
  id: string;
  name: string;
  freshness: string;
  quality: string;
  released: string;
  consumers: number;
  target: string;
}

export interface MissionHealthService {
  name: string;
  role: string;
  state: string;
}

export interface MissionQueueItem {
  name: string;
  state: string;
  detail: string;
}

export interface MissionRailStat {
  label: string;
  value: string;
  tone: Outcome;
}

/** Recorded rail service selection: `shown` names catalog services; `null` means unconfigured. */
export interface MissionServiceVisibility {
  shown: Array<string> | null;
  configured: boolean;
}

/** A catalog service the operator may include in the rail; `lifecycle` is "task" for one-shot jobs. */
export interface MissionServiceOption {
  name: string;
  role: string;
  state: string;
  lifecycle: string;
}

export interface MissionOverviewRail {
  id: string;
  ready_count: number;
  total_services: number;
  services: Array<MissionHealthService>;
  release_queue: Array<MissionQueueItem>;
  governance: Array<MissionRailStat>;
  recovery: Array<MissionRailStat>;
  service_options: Array<MissionServiceOption>;
  service_visibility: MissionServiceVisibility;
}

export interface MissionEnvironment {
  name: string;
  state: "ready" | "degraded" | "idle";
}

/* ------------------------------------------------------------------- runs */

export interface RunStageView {
  run_id: string;
  name: string;
  provider: string;
  outcome: string;
  offset_percent: number;
  width_percent: number;
  duration: string;
  note: string | null;
  flagged: boolean;
}

export interface RunQualitySampleRow {
  key: string;
  record_id: string;
  observed_at: string;
  occurrences: number;
}

export interface RunQualityFailure {
  run_id: string;
  check: string;
  verdict: string;
  detail: string;
  sample: Array<RunQualitySampleRow>;
  sample_total: number;
}

export interface RunEvent {
  run_id: string;
  at: string;
  level: "INFO" | "ERROR";
  message: string;
  attempt: number | null;
}

/** One quality outcome pinned to the run's attempt — execution status stays
 * distinct from evaluation result. */
export interface RunCheckOutcome {
  run_id: string;
  check: string;
  asset: string | null;
  stage: string | null;
  attempt: number;
  outcome: string;
  severity: string | null;
  blocking: boolean;
  evaluated: number | null;
  failed: number | null;
  tone: Outcome;
}

/** All quality outcomes recorded against one run attempt. */
export interface RunQualityReport {
  run_id: string;
  attempt: number | null;
  results: Array<RunCheckOutcome>;
  blocking_failure: RunQualityFailure | null;
}

/** One bounded page of a run's recorded events. */
export interface RunEventPage {
  items: Array<RunEvent>;
  next_cursor: string | null;
  total: number | null;
}

/** One bounded page of a run's retained log lines. */
export interface RunLogPage {
  items: Array<MissionRunLogLine>;
  next_cursor: string | null;
  total: number | null;
}

/** Explicit identity join for a run — logical, provider, attempt, report,
 * operation id, WAP launch digest and touched asset ids. */
export interface RunIdentity {
  run_id: string;
  durable_run_id: string | null;
  provider_run_id: string | null;
  attempt: number | null;
  report: string | null;
  pipeline: string | null;
  operation_id: string | null;
  launch_digest: string | null;
  asset_ids: Array<string>;
}

export interface RunSpan {
  run_id: string;
  name: string;
  duration: string;
  width_percent: number;
  tone: Outcome;
}

export interface RunArtifact {
  run_id: string;
  name: string;
  size: string;
  checksum: string;
}

export interface RunConsumer {
  run_id: string;
  name: string;
  role: string;
  current_snapshot: string | null;
}

export interface RunConfigurationRow {
  run_id: string;
  label: string;
  value: string;
}

/* ---------------------------------------------------------------- dataset */

export interface DatasetOwnership {
  owner: string | null;
  domain: string | null;
  freshness_target: string | null;
  schedule: string | null;
  classification: string | null;
  contract_version: string | null;
  retention: string | null;
}

export interface DatasetAccessGrant {
  principal: string;
  kind: "read" | "write" | "service";
  scope: string;
}

export interface DatasetRunRef {
  run_id: string;
  finished_at: string;
  outcome: string;
  release: string;
  duration: string;
}

export interface DatasetGovernance {
  id: string;
  dataset_id: string;
  ownership: DatasetOwnership;
  access: Array<DatasetAccessGrant>;
  runs: Array<DatasetRunRef>;
  stale: boolean;
  last_confirmed_at: string | null;
}

/* --------------------------------------------------------------- releases */

export interface ReleaseSummaryMetric {
  label: string;
  value: string;
  hint: string;
  tone: Outcome;
}

export interface ReleaseCandidate {
  /** Canonical candidate identity: the WAP logical run id, not the staging ref. */
  id: string;
  dataset: string;
  provider: string;
  strategy: string;
  readiness: string;
  evidence: string;
  created_at: string;
  action: string;
  run_id?: string | null;
  orchestrator_run_id?: string | null;
  staging_ref?: string | null;
  source_revision?: string | null;
  target_revision?: string | null;
  blockers?: Array<string>;
}

export interface CandidateSnapshotChange {
  table: string;
  released_snapshot: string;
  candidate_snapshot: string;
  row_delta: string;
}

export interface CandidateEvidenceRow {
  name: string;
  detail: string;
  outcome: string;
  tone: Outcome;
}

export interface PublicationPlanRow {
  label: string;
  value: string;
}

export interface ReleaseCandidateDetail {
  id: string;
  candidate: ReleaseCandidate | null;
  subtitle: string;
  status: string;
  revision: string;
  snapshot_changes: Array<CandidateSnapshotChange>;
  required_evidence: Array<CandidateEvidenceRow>;
  publication_plan: Array<PublicationPlanRow>;
  run_id?: string | null;
  orchestrator_run_id?: string | null;
  staging_ref?: string | null;
  strategy?: string | null;
  source_revision?: string | null;
  target_revision?: string | null;
  blockers?: Array<string>;
  failure_detail?: string | null;
}

/** Read-only promotion-gate evaluation, bound to its inputs by `digest`. */
export interface PromotionPreview {
  candidate_id: string;
  eligible: boolean;
  checks: Array<{
    name: string;
    outcome: string;
    detail: string;
    passed: boolean | null;
  }>;
  digest: string;
  strategy: string;
}

/**
 * The truthful outcome of one promotion command. `promoted` and
 * `already_promoted` (a competing promotion reconciled) are the only terminal
 * successes; every other outcome is a refusal or a resumable state and must
 * render its `blockers`/`failure_reason`, not a success affordance.
 */
export interface PromotionResult {
  outcome: string;
  candidate_id: string;
  blockers: Array<string>;
  preview_digest?: string;
  source_revision?: string;
  target_revision_before?: string;
  target_revision_after?: string;
  staging_ref?: string;
  source_deleted: boolean;
  resumed: boolean;
  failure_reason?: string;
  failure_detail?: string;
  release_revision?: string;
}

export interface CompletedRelease {
  id: string;
  dataset: string;
  provider: string;
  provider_strategy: string;
  reference: string;
  finished_at: string;
  outcome: string;
}

/* --------------------------------------------------------------- platform */

export interface PlatformSummaryMetric {
  label: string;
  value: string;
  hint: string;
  tone: Outcome;
}

export interface PlatformService {
  name: string;
  role: string;
  runtime_state: string;
  readiness_state: string;
  probe: string;
  action: string;
  attention: boolean;
  lifecycle: string;
}

export interface ServiceProbeFact {
  label: string;
  value: string;
}

export interface ServiceDependencyEdge {
  source: string;
  target: string;
  outcome: string;
  tone: Outcome;
}

export interface ServiceDiagnostics {
  id: string;
  name: string;
  readiness_state: string;
  summary: string;
  facts: Array<ServiceProbeFact>;
  dependencies: Array<ServiceDependencyEdge>;
  dependency_note: string;
  capability_checks: string;
  capabilities: Array<CandidateEvidenceRow>;
  stale: boolean;
  last_confirmed_at: string | null;
}

export interface CoverageRow {
  name: string;
  outcome: string;
  tone: Outcome;
}

/* ------------------------------------------------------------- governance */

export interface PublicationReview {
  dataset: string;
  owner: string;
  contract: string;
  verdict: string;
  reason: string;
  action: string;
  selected: boolean;
}

export interface AccessDriftEvidence {
  evidence: string;
  permissions: string;
  result: string;
  drifted: boolean;
}

export interface AccessDrift {
  dataset: string;
  verdict: string;
  subtitle: string;
  evidence: Array<AccessDriftEvidence>;
}

export interface OwnershipGap {
  dataset: string;
  requirement: string;
  owner: string;
  action: string;
}

export interface AuditEvent {
  at: string;
  actor: string;
  action: string;
  target: string;
  outcome: string;
  tone: Outcome;
}

/* -------------------------------------------------------------- workspace */

export interface ProviderConnection {
  name: string;
  role: string;
  endpoint: string;
  state: string;
  detail: string;
  action: string;
  degraded: boolean;
  credential_configured: boolean;
}

export interface ProviderImpact {
  id: string;
  provider: string;
  degraded: Array<string>;
  unaffected: Array<string>;
}

export interface NotificationRule {
  name: string;
  channel: string;
  urgency: string;
}

export interface WorkspaceMember {
  name: string;
  role: string;
  status: "active" | "invited" | "suspended";
}

export interface WorkspaceDefault {
  name: string;
  value: string;
}

/* --------------------------------------------------- run / dataset detail */

export interface MissionRunLogLine {
  run_id: string;
  at: string;
  level: "INFO" | "ERROR";
  message: string;
  attempt: number | null;
}

export interface SummaryMetricRow {
  label: string;
  value: string;
  hint: string;
  tone: Outcome;
}

export interface MissionRunDetail {
  id: string;
  workflow: string;
  run_id: string;
  status: string;
  summary: string;
  metrics: Array<SummaryMetricRow>;
  details: Array<RunConfigurationRow>;
  consumers: Array<RunConsumer>;
  artifacts: Array<RunArtifact>;
  identity: RunIdentity | null;
  failure?: string | null;
}

/* -------------------------------------------------------- run actions ---- */

/** Guarded run-action result — mirrors `RunActionResult` in the API. */
export type RunActionKind = "run.retry" | "run.cancel";
export type RunActionStatus =
  | "accepted"
  | "pending"
  | "reconciled"
  | "rejected"
  | "skipped";

export interface RunActionIdentity {
  run_id: string;
  project_id?: string | null;
  attempt?: number | null;
}

export interface RunActionResult {
  contract_version: number;
  action_kind: RunActionKind;
  status: RunActionStatus;
  verification_handle: string;
  target: RunActionIdentity;
  resulting_run?: RunActionIdentity | null;
  canonical_report_path?: string | null;
  provider: Record<string, unknown>;
  message: string;
}

/* ------------------------------------------------- services + assets ----- */

/** Substrate service summary — mirrors `ObservatoryService`. */
export interface ObservatoryService {
  id: string;
  name: string;
  kind: string;
  status: string;
  runtime_state: string;
  in_stack: boolean;
  depends_on: Array<string>;
  impacts: Array<string>;
}

/** Substrate service detail — mirrors `ObservatoryServiceDetail`. */
export interface ObservatoryServiceDetail {
  service: ObservatoryService;
  dependencies: Array<ObservatoryService>;
  dependents: Array<ObservatoryService>;
  actions: Array<{
    id: string;
    label: string;
    kind: string;
    enabled: boolean;
    reason?: string | null;
    risk_level?: string | null;
    equivalent_cli_command?: string | null;
    expected_evidence?: Array<string>;
  }>;
}

/** Generic guarded-action result — mirrors `ObservatoryActionResult`. */
export interface ObservatoryActionResult {
  action: { id: string; label: string; kind: string };
  status: "succeeded" | "failed" | "skipped" | "accepted" | "running" | "unknown";
  message: string;
}

/** Provider materialize reply — Dagster's operation response shape. */
export interface MaterializeResult {
  operation?: string;
  dry_run?: boolean;
  accepted?: boolean;
  run_id?: string | null;
  asset_key_path?: string | null;
  status?: string;
  message?: string;
}

export interface DatasetSchemaField {
  field: string;
  type: string;
  nullable: string;
  role: string;
}

export interface LineageNode {
  name: string;
  role: string;
  current: boolean;
}

export interface DatasetCheck {
  name: string;
  outcome: string;
  tone: Outcome;
}

export interface DatasetPreview {
  columns: Array<string>;
  rows: Array<Array<string>>;
  /** Relation/table identity the preview was read from. */
  ref: string | null;
  /** True only when the preview was read against a recorded snapshot. */
  pinned: boolean;
  /** ready | relation_missing | unavailable | no_table */
  state: string;
  detail: string | null;
  has_more: boolean;
  limit: number;
  offset: number;
}

export interface MissionDatasetDetail {
  id: string;
  name: string;
  status: string;
  summary: string;
  metrics: Array<SummaryMetricRow>;
  schema_fields: Array<DatasetSchemaField>;
  preview: DatasetPreview;
  checks: Array<DatasetCheck>;
  lineage: Array<LineageNode>;
  ownership: DatasetOwnership;
  access: Array<DatasetAccessGrant>;
  runs: Array<DatasetRunRef>;
  /** ready | unavailable — whether the run source answered. */
  runs_state: string;
}

export interface PublicationPlan {
  id: string;
  dataset_id: string;
  subtitle: string;
  status: string;
  rows: Array<PublicationPlanRow>;
}

/** A declared asset from the project's provider registry. */
export interface ObservatoryAsset {
  id: string;
  name: string;
  group: string | null;
  description: string | null;
  kinds: Array<string>;
  dependencies: Array<string>;
  checks: Array<string>;
}

/** Provider-neutral dataset summary from the substrate `/datasets` read. */
export interface ObservatoryDataset {
  id: string;
  name: string;
  description: string | null;
  owner: string | null;
  classifications: Array<string>;
  publication_state: string;
  readiness_state: string;
  candidate: boolean;
  kinds: Array<string>;
}

/** Provider-neutral orchestrator run summary from the substrate `/runs` read. */
export interface ObservatoryRun {
  id: string;
  name: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  assets: Array<{ kind: string; id: string; label: string }>;
}

/** One row of the mission run list — same population as the counters. */
export interface MissionRunRow {
  id: string;
  name: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  asset_ids: Array<string>;
}

/** Bounded, cursor-paginated run list. */
export interface MissionRunList {
  items: Array<MissionRunRow>;
  next_cursor: string | null;
}

/** Cursor-paginated substrate list envelope. */
export interface ObservatoryListPage<T> {
  items: Array<T>;
  next_cursor: string | null;
}
