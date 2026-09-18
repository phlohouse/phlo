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

export interface MissionOverviewRail {
  id: string;
  ready_count: number;
  total_services: number;
  services: Array<MissionHealthService>;
  release_queue: Array<MissionQueueItem>;
  governance: Array<MissionRailStat>;
  recovery: Array<MissionRailStat>;
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
  id: string;
  dataset: string;
  provider: string;
  strategy: string;
  readiness: string;
  evidence: string;
  created_at: string;
  action: string;
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
