/**
 * Shared wire contracts between the Observatory UI and phlo-api.
 *
 * Types that pass through from backend JSON keep snake_case field names;
 * shapes built or normalized client-side use camelCase. These are transport
 * contracts, not internal models: renaming a field here silently breaks the
 * matching Python serializer.
 */
export type ObservatoryHealthState = 'ok' | 'warning' | 'error' | 'unknown'

export type ObservatoryMetadata = Record<string, NonNullable<unknown>>
type ObservatoryRecord = Record<string, NonNullable<unknown>>

export type ObservatoryServiceStatus =
  | 'running'
  | 'stopped'
  | 'unhealthy'
  | 'starting'
  | 'unknown'

interface ObservatoryHealth {
  state: ObservatoryHealthState
  message?: string | null
}

interface ObservatoryExternalLink {
  label: string
  url: string
  kind: string
}

export interface ObservatoryCapabilityPage {
  id: string
  label: string
  path: string
  available: boolean
  nav: boolean
  reason?: string | null
  providers: Array<string>
  metadata: ObservatoryMetadata
}

export interface ObservatoryCapabilities {
  version: number
  pages: Array<ObservatoryCapabilityPage>
  features: Record<string, boolean>
  providers: Record<string, Array<string>>
}

export interface ObservatoryWorkflowWizardField {
  name: string
  label: string
  field_type: 'text' | 'textarea' | 'select' | 'checkbox' | 'fields'
  required: boolean
  description?: string | null
  default?: string | number | boolean
  options: Array<string>
  secret: boolean
}

export interface ObservatoryWorkflowWizardContribution {
  id: string
  package: string
  stage: 'source' | 'transform' | 'quality' | 'publish'
  label: string
  description: string
  required_capabilities: Array<string>
  optional_capabilities: Array<string>
  fields: Array<ObservatoryWorkflowWizardField>
  modes: Array<'proposal' | 'apply'>
  metadata: ObservatoryMetadata
}

export interface ObservatoryWorkflowWizardPayload {
  version: number
  stages: Array<'source' | 'transform' | 'quality' | 'publish'>
  contributions: Array<ObservatoryWorkflowWizardContribution>
}

interface ObservatoryWorkflowGraphNode {
  id: string
  contribution_id: string
  stage: 'source' | 'transform' | 'quality' | 'publish'
  values: Record<string, unknown>
}

interface ObservatoryWorkflowGraphEdge {
  id: string
  source: string
  target: string
}

export interface ObservatoryWorkflowGraph {
  nodes: Array<ObservatoryWorkflowGraphNode>
  edges: Array<ObservatoryWorkflowGraphEdge>
}

export interface ObservatoryWorkflowProposalRequest {
  workflow_name: string
  domain: string
  graph: ObservatoryWorkflowGraph
}

interface ObservatoryWorkflowFilePreview {
  path: string
  content: string
  mode: 'create' | 'modify'
}

export interface ObservatoryWorkflowApplyAction {
  id: string
  label: string
  target_files: Array<string>
  conflict_policy: 'preview' | 'skip-if-exists' | 'fail-on-conflict'
  enabled: boolean
  reason?: string | null
  risk_level: 'low' | 'medium' | 'high' | 'critical'
  required_permission?: string | null
  expected_evidence: Array<string>
}

export interface ObservatoryWorkflowProposal {
  proposal_id: string
  workflow_name: string
  domain: string
  selected_contributions: Array<string>
  planned_assets: Array<string>
  planned_tables: Array<string>
  planned_models: Array<string>
  files: Array<ObservatoryWorkflowFilePreview>
  warnings: Array<string>
  missing_capabilities: Array<string>
  disabled_stages: Record<string, string>
  actions: Array<ObservatoryWorkflowApplyAction>
}

export interface ObservatoryWorkflowActionResult {
  action_id: string
  status: 'succeeded' | 'failed' | 'skipped'
  message: string
  files: Array<string>
}

export interface ObservatoryResourceRef {
  kind: string
  id: string
  label: string
}

export interface ObservatoryDataset {
  id: string
  name: string
  description?: string | null
  owner?: string | null
  classifications: Array<string>
  publication_state: 'draft' | 'published' | 'retired'
  readiness_state: ObservatoryHealthState
  candidate: boolean
  kinds: Array<string>
  source_refs: Array<ObservatoryResourceRef>
  metadata: ObservatoryMetadata
}

export interface ObservatoryPublishingReadinessItem {
  dataset_id: string
  publishing: ObservatoryPublishingReadiness
}

export type ObservatoryControlStatus =
  | 'pass'
  | 'fail'
  | 'warning'
  | 'unknown'
  | 'not_applicable'

export interface ObservatoryControlEvidence {
  kind: string
  id: string
  label: string
  value?: string | null
  resource?: ObservatoryResourceRef | null
  metadata: ObservatoryMetadata
}

export interface ObservatoryDatasetControl {
  id: string
  label: string
  status: ObservatoryControlStatus
  message?: string | null
  evidence: Array<ObservatoryControlEvidence>
}

export interface ObservatoryGovernanceRow {
  dataset: ObservatoryDataset
  owner?: string | null
  classifications: Array<string>
  status: ObservatoryControlStatus
  controls: Array<ObservatoryDatasetControl>
}

export interface ObservatoryGovernanceMatrix {
  controls: Array<string>
  rows: Array<ObservatoryGovernanceRow>
  status_counts: Record<string, number>
}

export interface ObservatoryTelemetryPrivacyPolicy {
  identity_detail: 'anonymous' | 'aggregate' | 'identity' | 'audit_only'
  retention_days?: number | null
  audit_drilldown: boolean
  metadata: ObservatoryMetadata
}

export interface ObservatoryAccessActivity {
  id: string
  action: string
  actor_label?: string | null
  actor_kind?: string | null
  count: number
  last_seen_at?: string | null
  metadata: ObservatoryMetadata
}

export interface ObservatoryDependencyActivity {
  id: string
  source: ObservatoryResourceRef
  target: ObservatoryResourceRef
  kind: string
  count: number
  last_seen_at?: string | null
  metadata: ObservatoryMetadata
}

export interface ObservatoryConsumerAdoption {
  id: string
  consumer: string
  kind: string
  owner?: string | null
  status: string
  declared_at?: string | null
  metadata: ObservatoryMetadata
}

export interface ObservatoryDatasetUsage {
  privacy_policy: ObservatoryTelemetryPrivacyPolicy
  access_activity: Array<ObservatoryAccessActivity>
  dependency_activity: Array<ObservatoryDependencyActivity>
  consumer_adoption: Array<ObservatoryConsumerAdoption>
}

export interface ObservatoryDatasetWorkflowConfig {
  default_owner: string
  approval_states: Array<string>
}

export interface ObservatoryPublishingAction {
  id: string
  label: string
  enabled: boolean
  reason?: string | null
  consequences: Array<string>
}

export interface ObservatoryPublishingReadiness {
  state: ObservatoryHealthState
  policy_name: string
  internal_only: boolean
  blockers: Array<string>
  warnings: Array<string>
  missing_evidence: Array<string>
  actions: Array<ObservatoryPublishingAction>
}

export interface ObservatoryPipelineStage {
  id: string
  label: string
  state: ObservatoryHealthState
  resource?: ObservatoryResourceRef | null
}

export interface ObservatoryDatasetPipeline {
  dataset?: ObservatoryDataset | null
  freshness_state: ObservatoryHealthState
  freshness_at?: string | null
  last_run?: ObservatoryResourceRef | null
  stages: Array<ObservatoryPipelineStage>
  actions: Array<ObservatoryAction>
}

export interface ObservatoryDatasetProfile {
  dataset: ObservatoryDataset
  asset?: ObservatoryAsset | null
  tables: Array<ObservatoryTable>
  quality: Array<ObservatoryQualityCheck>
  upstream: Array<ObservatoryResourceRef>
  downstream: Array<ObservatoryResourceRef>
  logs: Array<ObservatoryLogEvent>
  operations: Array<ObservatoryOperation>
  governance: Array<ObservatoryDatasetControl>
  usage: ObservatoryDatasetUsage
  publishing: ObservatoryPublishingReadiness
  pipeline: ObservatoryDatasetPipeline
  sections: Record<string, boolean>
}

export interface ObservatoryAction {
  id: string
  label: string
  kind: string
  enabled: boolean
  requires_confirmation: boolean
  reason?: string | null
  risk_level: 'low' | 'medium' | 'high' | 'critical'
  required_capability?: string | null
  required_service?: string | null
  required_permission?: string | null
  equivalent_cli_command?: string | null
  expected_evidence: Array<string>
  background_operation_id?: string | null
}

export interface ObservatoryActionResult {
  action: ObservatoryAction
  status: 'succeeded' | 'failed' | 'skipped'
  message: string
  operation?: ObservatoryOperation | null
}

interface ObservatoryServicePort {
  name: string
  target: string
  published?: string | null
}

interface ObservatoryServiceConfigEntry {
  name: string
  value?: string | null
  description?: string | null
  secret: boolean
}

export interface ObservatoryService {
  id: string
  name: string
  kind: string
  status: ObservatoryServiceStatus
  health: ObservatoryHealth
  definition_state?: 'configured' | 'available'
  runtime_state?: ObservatoryServiceStatus
  in_stack?: boolean
  disabled?: boolean
  profile?: string | null
  backend?: string
  depends_on: Array<string>
  impacts: Array<string>
  links: Array<ObservatoryExternalLink>
  metadata: ObservatoryMetadata
}

export interface ObservatoryServiceDetail {
  service: ObservatoryService
  dependencies: Array<ObservatoryService>
  dependents: Array<ObservatoryService>
  actions: Array<ObservatoryAction>
  logs: Array<ObservatoryLogEvent>
  ports: Array<ObservatoryServicePort>
  config: Array<ObservatoryServiceConfigEntry>
}

export interface ObservatoryPackageInstallResult {
  package_name: string
  package_spec: string
  status: 'succeeded' | 'failed' | 'skipped'
  message: string
  services: Array<string>
}

export interface ObservatoryOverviewRow {
  id: string
  kind: 'service' | 'quality' | 'operation' | 'log'
  label: string
  href: string
  state: ObservatoryHealthState
  meta?: string | null
  reason?: string | null
}

export interface ObservatoryOverview {
  health: ObservatoryHealth
  counters: Record<string, number>
  attention: Array<ObservatoryOverviewRow>
  events: Array<ObservatoryOverviewRow>
  recent: Array<ObservatoryResourceRef>
}

export interface ObservatoryResourceItem {
  id: string
  name: string
  kind: string
  health?: ObservatoryHealth | null
  status?: string | null
  summary?: string | null
  updated_at?: string | null
  links?: Array<ObservatoryExternalLink>
  metadata: ObservatoryMetadata
}

export interface ObservatorySurfaceItem {
  id: string
  name: string
  kind: string
  health: ObservatoryHealth
  summary?: string | null
  metadata: ObservatoryMetadata
}

export interface ObservatoryOperation {
  id: string
  name: string
  kind: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'skipped' | 'unknown'
  health: ObservatoryHealth
  target?: ObservatoryResourceRef | null
  started_at?: string | null
  completed_at?: string | null
  duration_seconds?: number | null
  metadata: ObservatoryMetadata
}

export interface ObservatoryOperationDetail {
  operation: ObservatoryOperation
  related: Array<ObservatoryResourceRef>
  logs: Array<ObservatoryLogEvent>
  actions: Array<ObservatoryAction>
}

type ObservatoryRunStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'unknown'

export interface ObservatoryRunReportIdentity {
  project_id: string
  run_id: string
  attempt: number
}

export interface ObservatoryRun {
  id: string
  name: string
  status: ObservatoryRunStatus
  started_at?: string | null
  completed_at?: string | null
  duration_seconds?: number | null
  assets: Array<ObservatoryResourceRef>
  checks: Array<ObservatoryResourceRef>
  logs: Array<ObservatoryResourceRef>
  metadata: ObservatoryMetadata
  report_identity?: ObservatoryRunReportIdentity | null
}

export interface ObservatoryReportGap {
  field: string
  status: string
  reason: string
}

export interface ObservatoryReportEvent {
  event_id: string
  producer: string
  event_type: string
  observed_at?: string | null
  sequence?: number | null
  payload_checksum?: string | null
}

export interface ObservatoryReportStage {
  stage_id: string
  stage_type: string
  provider?: string | null
  tool?: string | null
  asset?: string | null
  status: string
  started_at?: string | null
  finished_at?: string | null
  error_fingerprint?: string | null
}

export interface ObservatoryReportResource {
  resource_id: string
  resource_kind: string
  role: string
  normalized_identity?: string | null
  uri?: string | null
  table_name?: string | null
  catalog?: string | null
  ref_name?: string | null
  schema_hash?: string | null
  record_count?: number | null
  byte_count?: number | null
  staged_objects: Array<Record<string, string | number | boolean | null>>
  snapshot_before?: string | null
  snapshot_after?: string | null
}

export interface ObservatoryReportLineage {
  lineage_edge_id: string
  source: string
  target: string
  origin: string
  derivation: string
}

export interface ObservatoryReportQuality {
  quality_result_id: string
  check_id: string
  asset?: string | null
  stage_id?: string | null
  severity?: string | null
  blocking: boolean
  passed: boolean
  evaluated_count?: number | null
  failed_count?: number | null
  failure_artifact_id?: string | null
}

export interface ObservatoryReportCatalogChange {
  catalog_change_id: string
  catalog_ref?: string | null
  content_key?: string | null
  operation: string
  source_hash?: string | null
  target_hash?: string | null
  commit_hash?: string | null
  merge_outcome?: string | null
  snapshot_before?: string | null
  snapshot_after?: string | null
  metadata: Record<string, string | number | boolean | null>
}

export interface ObservatoryReportArtifact {
  artifact_id: string
  artifact_kind: string
  uri?: string | null
  content_type?: string | null
  checksum?: string | null
  expires_at?: string | null
  legal_hold: boolean
  status: string
}

export interface ObservatoryReportRun {
  project_id: string
  run_id: string
  pipeline_name?: string | null
  provider_run_id?: string | null
  attempt: number
  status: string
  started_at?: string | null
  finished_at?: string | null
  failure_summary?: string | null
  evidence_completeness: string
}

export interface ObservatoryReportLifecycle {
  run?: ObservatoryReportRun | null
  events: Array<ObservatoryReportEvent>
}

export interface ObservatoryReportTerminalOutcome {
  status: string
  source: string
  evidence_id: string
  observed_at?: string | null
}

export interface ObservatoryRunReport {
  schema_version: number
  project_id: string
  run_id: string
  attempt: number
  lifecycle: ObservatoryReportLifecycle
  stages: Array<ObservatoryReportStage>
  inputs: Array<ObservatoryReportResource>
  staging: Array<ObservatoryReportResource>
  outputs: Array<ObservatoryReportResource>
  lineage: Array<ObservatoryReportLineage>
  transformations: Array<ObservatoryReportStage>
  quality: Array<ObservatoryReportQuality>
  iceberg_snapshots: Array<ObservatoryReportResource>
  catalog_changes: Array<ObservatoryReportCatalogChange>
  artifacts: Array<ObservatoryReportArtifact>
  terminal_outcome?: ObservatoryReportTerminalOutcome | null
  gaps: Array<ObservatoryReportGap>
}

export interface ObservatoryAsset {
  id: string
  name: string
  group?: string | null
  description?: string | null
  kinds: Array<string>
  dependencies: Array<string>
  resources: Array<string>
  checks: Array<string>
  metadata: ObservatoryMetadata
}

export interface ObservatoryAssetDetail {
  asset: ObservatoryAsset
  upstream: Array<ObservatoryAsset>
  downstream: Array<ObservatoryAsset>
  tables: Array<ObservatoryTable>
  quality: Array<ObservatoryQualityCheck>
  logs: Array<ObservatoryLogEvent>
  operations: Array<ObservatoryOperation>
  lineage: Array<ObservatoryResourceRef>
  materializations: Array<ObservatoryOperation>
  column_lineage: Record<string, Array<string>>
}

export interface ObservatoryTable {
  id: string
  name: string
  namespace?: string | null
  asset_id?: string | null
  format?: string | null
  branch?: string | null
  schema_name?: string | null
  metadata: ObservatoryMetadata
}

export interface ObservatoryTablePreview {
  table: ObservatoryTable
  columns: Array<string>
  column_types: Array<string>
  rows: Array<ObservatoryRecord>
  row_count?: number | null
  limit: number
  offset: number
  has_more: boolean
}

export interface ObservatoryQueryResult {
  columns: Array<string>
  rows: Array<ObservatoryRecord>
  row_count?: number | null
  effective_sql: string
  limit: number
  offset: number
  warnings: Array<string>
}

export interface ObservatorySavedQuery {
  id: string
  name: string
  sql: string
  branch?: string | null
  created_at: string
  updated_at: string
  metadata: ObservatoryMetadata
}

export interface ObservatoryRowJourney {
  table: ObservatoryTable
  row_id: string
  row: ObservatoryRecord
  upstream: Array<ObservatoryResourceRef>
  downstream: Array<ObservatoryResourceRef>
  stages: Array<ObservatoryResourceRef>
  logs: Array<ObservatoryLogEvent>
  diff: ObservatoryRecord
}

export interface ObservatoryQualityCheck {
  id: string
  name: string
  asset_id: string
  status: 'passing' | 'failing' | 'warning' | 'unknown'
  severity?: string | null
  blocking: boolean
  description?: string | null
  metadata: ObservatoryMetadata
}

export interface ObservatoryQualityDetail {
  check: ObservatoryQualityCheck
  asset?: ObservatoryAsset | null
  history: Array<ObservatoryOperation>
  logs: Array<ObservatoryLogEvent>
  actions: Array<ObservatoryAction>
}

export interface ObservatoryLogEvent {
  id: string
  timestamp?: string | null
  level: string
  message: string
  source?: string | null
  resource?: ObservatoryResourceRef | null
  metadata: ObservatoryMetadata
}

export interface ObservatoryLogFacets {
  sources: Array<string>
  levels: Array<string>
  resources: Array<ObservatoryResourceRef>
}

export interface ObservatoryBranch {
  id: string
  name: string
  current: boolean
  protected: boolean
  metadata: ObservatoryMetadata
}

export interface ObservatoryBranchDetail {
  branch: ObservatoryBranch
  contents: Array<ObservatoryResourceRef>
  commits: Array<ObservatoryOperation>
  compare: Record<string, number>
  tables: Array<ObservatoryTable>
}

export interface ObservatoryRuntimeSettings {
  version: number
  defaults: Record<string, string>
  features: Record<string, boolean>
  storage: Record<string, string>
  metadata: ObservatoryMetadata & {
    runtime?: {
      project_path?: string | null
      compose_project?: string | null
      api_source?: string | null
    }
    providers?: Record<string, Array<string>>
  }
}

export interface ObservatoryExtension {
  id: string
  name: string
  version?: string | null
  enabled: boolean
  routes: Array<string>
  nav: Array<string>
  settings_scope?: string | null
  metadata: ObservatoryMetadata
}

export interface ObservatoryExtensionDetail {
  extension: ObservatoryExtension
  routes: Array<string>
  nav: Array<string>
  capabilities: Array<ObservatoryResourceRef>
}

export interface ObservatorySearchResult {
  id: string
  label: string
  kind: string
  summary?: string | null
  href?: string | null
  metadata: ObservatoryMetadata
}

export interface ObservatoryResourceResult<T> {
  data: T | null
  error: string | null
}
