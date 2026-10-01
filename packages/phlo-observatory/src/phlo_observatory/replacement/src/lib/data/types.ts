/** Shared domain and presentation types for phlo. */

export type Env = 'prod' | 'staging'
export type Layer = 'bronze' | 'silver' | 'gold'
export type LayerOrCatalog = Layer | 'catalog'
export type Severity = 'high' | 'medium' | 'low'
export type Tone = 'bad' | 'warn' | 'ok' | 'branch' | 'info' | 'neutral'

export type IncidentStatus =
  | 'investigating'
  | 'in-progress'
  | 'triage'
  | 'blocked'
  | 'monitoring'
  | 'resolved'

export type IncidentKind =
  | 'freshness'
  | 'schema'
  | 'audit'
  | 'catalog'
  | 'performance'
  | 'maintenance'

export interface Incident {
  id: string // "214"
  title: string // "Stale table: bioreactor telemetry"
  headline: string // page heading, e.g. "Stale bronze table"
  kind: IncidentKind
  layer: LayerOrCatalog
  severity: Severity
  status: IncidentStatus
  owner: string
  /** e.g. "52 min", "1 d" */
  age: string
  assetId?: string
  /** For resolved incidents, e.g. "Resolved in 3 h 12 m" */
  resolvedIn?: string
  resolvedAgo?: string
}

export type AssetHealth = 'stale' | 'warn' | 'ok'
/** One character per day: g = met SLA, a = late, r = breached */
export type DayCode = 'g' | 'a' | 'r'

export interface AssetTag {
  tone: Tone
  label: string
  incidentId?: string
}

export interface Asset {
  id: string // fully qualified name, e.g. "bronze.bioreactor_telemetry"
  layer: Layer
  health: AssetHealth
  lag: string // "134 m / 60 m"
  lastMaterialized: string
  rows: string
  size: string
  owner: string
  days: Array<DayCode>
  tag?: AssetTag
}

/** One character per run: s = succeeded, w = slow, f = failed, k = skipped, p = paused, n = none */
export type RunCode = 's' | 'w' | 'f' | 'k' | 'p' | 'n'

export type JobStatus = 'failing' | 'slow' | 'ok' | 'paused' | 'waiting'

export interface Job {
  name: string
  kind: string // "Ingest · dlt"
  domain: string
  status: JobStatus
  statusLabel: string
  reason?: string
  cron: string
  next: string
  runs: Array<RunCode> // oldest → newest
  avg: string
  owner: string
  lastRun: string
  incidentId?: string
}

export interface JobGroup {
  name: string
  jobs: number
  note: string
  ok: number
  bad: number
  badTone: 'bad' | 'warn' | 'neutral'
  rate: string
  owner: string
}

export type ServiceState = 'up' | 'slow' | 'down'
export interface Service {
  name: string
  state: ServiceState
  detail?: string
}

export interface LayerSummary {
  layer: Layer
  tables: number
  stale: number
  note: string
  size: string
  description: string
}

export interface Source {
  name: string
  tone: Tone
  lag: string
}

export interface Kpis {
  freshness: { pct: string; fresh: number; total: number }
  runs: { total: number; failed: number; successRate: string; median: string }
  audits: { passing: number; total: number; failingModels: number }
  incidents: { open: number; high: number; oldest: string }
}

export interface ActivityItem {
  tone: Tone
  text: string // plain text; mono spans marked with `backticks`
  ago: string
  href?: string
}

export interface Branch {
  name: string
  kind: 'main' | 'fix' | 'feat' | 'dev'
  owner: string
  head: string
  ahead: number
  behind: number
  status: { tone: Tone; label: string }
  note: string
  incidentId?: string
}

export interface ReleaseTag {
  name: string
  commit: string
}

export type Role = 'Admin, Approver' | 'Approver' | 'Engineer' | 'Viewer'
export interface Member {
  initials: string
  name: string
  note?: string
  team: string
  role: Role
  canSign: 'yes' | 'no' | 'setup'
  lastActive: string
  you?: boolean
}

export interface ServiceAccount {
  name: string
  allowed: string
  key: string
  keyTone?: Tone
}

export interface AuditEvent {
  id: string
  time: string
  day: string
  actor: string
  actorKind: string
  action: string
  object: string
  signature?: { tone: Tone; label: string }
}

export interface StagingDiff {
  group: 'jobs' | 'contracts' | 'audits' | 'config'
  change: 'add' | 'change' | 'remove'
  name: string
  detail: string
  status: { tone: Tone; label: string }
}

export interface Promotion {
  title: string
  who: string
  tone: Tone
  ready: boolean
  why?: string
  checks: Array<{ state: 'pass' | 'fail' | 'warn' | 'pending'; label: string }>
}

export interface Commit {
  id: string
  message: string
  who: string
  ago: string
}

export interface BranchGraph {
  base: string
  commits: Array<string>
  /** Commits on main since the fork. */
  behind: number
  mergeable: boolean
}

export interface AssetColumn {
  name: string
  type: string
  nullable: boolean
  note?: string
  /** Renamed on a work branch, not yet on main. */
  renamedTo?: string
}

export type RunBarState = 'ok' | 'skip' | 'fail'
export interface RunBar {
  value: number
  state: RunBarState
}

export interface AssetAudit {
  name: string
  kind: string
  result: string
  failing: boolean
  history: string
  blocks: string
}

export interface SchemaVersion {
  version: string
  ref?: { tone: Tone; label: string }
  when: string
  /** Backticks mark mono spans. */
  change: string
  note: string
  current?: boolean
}

export interface SchemaDiffLine {
  kind: ' ' | '+' | '-'
  name: string
  type: string
  extra?: string
}

export interface CodeUsage {
  file: string
  where: string
  status: { tone: Tone; label: string }
}

export type LineageTone = 'source' | 'job' | 'self' | 'bad' | 'ok' | 'warn'
export interface LineageNode {
  id: string
  sub: string
  name: string
  tone: LineageTone
  subBad?: boolean
  href?: string
}

export interface LineageColumn {
  heading: string
  wide?: boolean
  nodes: Array<LineageNode>
}

export interface Snapshot {
  id: string
  when: string
  op: string
  rows: string
  files: string
  ref: string
  refKind: 'main' | 'fix' | 'tag'
  by: string
}

export interface MaterializeMode {
  value: 'latest' | 'backfill' | 'full'
  title: string
  hint: string
  estimate: {
    rows: string
    runs: string
    time: string
    cost: string
    cta: string
  }
}

export interface AssetDetail {
  description: string
  format: string
  source: string
  job: string
  sla: string
  files: string
  sortOrder: string
  contract: string
  columns: Array<AssetColumn>
  runs: Array<RunBar>
  audits: Array<AssetAudit>
  /** Up to four audits shown on the Overview side panel. */
  overviewAudits: Array<AssetAudit>
  downstream: Array<{ id: string; layer: Layer }>
  downstreamMore?: string
  /** Work branch this asset is being fixed on, if any. */
  workBranch?: string
  schema: {
    versions: Array<SchemaVersion>
    count: number
    diffTitle: string
    diffNote: string
    diffBadge?: { tone: Tone; label: string }
    diff: Array<SchemaDiffLine>
    usagesTitle: string
    usages: Array<CodeUsage>
  }
  lineage: {
    columns: Array<LineageColumn>
    edges: Array<[string, string]>
    label: string
  }
  columnUse: Array<[string, string]>
  reports: Array<{ name: string; status: { tone?: Tone; label: string } }>
  snapshotStats: Array<{
    label: string
    value: string
    tone?: 'bad' | 'warn' | 'ok'
  }>
  snapshots: Array<Snapshot>
  timeTravelId: string
  data: {
    filters: Array<{ column: string; label: string; value?: string }>
    branches: Array<string>
    rows: Array<Array<string>>
    shown: string
    asOf: string
    newest?: { tone: 'warn' | 'muted'; text: string }
  }
  materialize: {
    via: string
    modes: Array<MaterializeMode>
    rebuild?: string
  }
  addAudit: {
    dryRun: string
    footer: string
  }
}

export type ScaleStatus = 'failing' | 'slow' | 'paused' | 'ok'
