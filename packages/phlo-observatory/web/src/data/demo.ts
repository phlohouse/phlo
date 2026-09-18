/**
 * Stage 1 demo data.
 *
 * Every export here stands in for a phlo-api response. Types are the contract
 * the API layer will satisfy in stage 2 — see `API_NEEDS.md` for the mapping
 * from each type to its endpoint.
 */

export type Severity = "danger" | "warning" | "accent" | "muted";

/* ------------------------------------------------------------------ shell */

export const environments = ["Production", "Staging", "Development"] as const;
export type Environment = (typeof environments)[number];

export interface Alert {
  severity: Severity;
  title: string;
  detail: string;
  action?: string;
  time: string;
}

export const alerts: Array<Alert> = [
  {
    severity: "danger",
    title: "Release blocked",
    detail: "rel-0193 held at the evidence gate — Pandera uniqueness",
    action: "Open release →",
    time: "09:31",
  },
  {
    severity: "danger",
    title: "Evidence degraded",
    detail: "Polaris unreachable — figures shown as last confirmed 09:21",
    action: "Inspect provider →",
    time: "09:22",
  },
  {
    severity: "danger",
    title: "Run failed",
    detail: "orders_daily r7e42b — uniqueness check on order_id",
    action: "Open run →",
    time: "09:14",
  },
  {
    severity: "accent",
    title: "Unknown outcome",
    detail: "op-7c41 applied but unconfirmed — reconcile, don't retry",
    action: "Reconcile →",
    time: "08:47",
  },
  {
    severity: "muted",
    title: "Digest delivered",
    detail: "Nightly summary sent to #phlo-ops — recorded in Audit",
    time: "08:02",
  },
];

export interface SearchEntry {
  kind: "Dataset" | "Run" | "Release" | "Service" | "Page";
  title: string;
  meta: string;
  to: string;
}

export const searchEntries: Array<SearchEntry> = [
  { kind: "Dataset", title: "Orders", meta: "marts.orders · Fresh", to: "/datasets/orders" },
  { kind: "Dataset", title: "Customer profiles", meta: "35m late", to: "/datasets/orders" },
  { kind: "Run", title: "orders_daily r7e42b", meta: "Failed validation", to: "/runs/orders-daily" },
  { kind: "Run", title: "sales_marts", meta: "Transform · running", to: "/runs/orders-daily" },
  { kind: "Release", title: "inventory_daily", meta: "Blocked · evidence", to: "/releases" },
  { kind: "Release", title: "product_catalog", meta: "Ready · awaiting promotion", to: "/releases" },
  { kind: "Service", title: "OTel collector", meta: "Telemetry · Delayed", to: "/platform" },
  { kind: "Page", title: "Governance", meta: "Policy and publication", to: "/governance" },
];

/* --------------------------------------------------------------- overview */

export interface ServiceHealth {
  name: string;
  role: string;
  state: "Ready" | "Delayed" | "Down";
}

export const services: Array<ServiceHealth> = [
  { name: "Dagster", role: "Orchestration", state: "Ready" },
  { name: "Nessie", role: "Catalog", state: "Ready" },
  { name: "MinIO", role: "Object storage", state: "Ready" },
  { name: "Trino", role: "Query engine", state: "Ready" },
  { name: "Postgres", role: "Serving & state", state: "Ready" },
  { name: "OTel collector", role: "Telemetry", state: "Delayed" },
  { name: "Polaris", role: "Catalog", state: "Ready" },
  { name: "Superset", role: "BI", state: "Ready" },
  { name: "Kafka", role: "Streaming", state: "Ready" },
  { name: "Airbyte", role: "Ingestion", state: "Ready" },
  { name: "dbt", role: "Transform", state: "Ready" },
  { name: "Prometheus", role: "Metrics", state: "Ready" },
];

export interface SummaryMetric {
  label: string;
  value: string;
  hint: string;
}

export const summaryMetrics: Array<SummaryMetric> = [
  { label: "Data", value: "128 datasets", hint: "121 fresh · 4 late · 3 unknown" },
  { label: "Ingestion", value: "18 sources", hint: "16 current · 1 delayed · 1 idle" },
  { label: "Quality", value: "612 checks", hint: "608 passed · 3 warnings · 1 failed" },
  { label: "Execution", value: "156 runs", hint: "149 succeeded · 4 active · 3 failed" },
  { label: "Releases", value: "2 pending", hint: "1 ready · 1 evidence blocked" },
  { label: "Governance", value: "124 owned", hint: "4 unassigned · 2 reviews due" },
];

export interface AttentionItem {
  severity: Severity;
  title: string;
  detail: string;
  action: string;
  to: string;
}

export const attentionItems: Array<AttentionItem> = [
  {
    severity: "danger",
    title: "Orders delivery blocked",
    detail: "42 duplicate IDs · Revenue dashboard + 2 consumers · 8m",
    action: "Inspect run",
    to: "/runs/orders-daily",
  },
  {
    severity: "warning",
    title: "Customer profiles are 35m late",
    detail: "09:00 delivery missed · CRM sync · Data platform",
    action: "View dataset",
    to: "/datasets/orders",
  },
  {
    severity: "muted",
    title: "Inventory release lacks evidence",
    detail: "Write succeeded · Validation missing · Released data unchanged",
    action: "Review",
    to: "/releases",
  },
];

export interface ExecutionRow {
  workflow: string;
  stage: string;
  progress: string;
  elapsed: string;
  to: string;
}

export const activeExecution: Array<ExecutionRow> = [
  { workflow: "orders_incremental", stage: "Ingest", progress: "1.2m rows staged", elapsed: "04:12", to: "/runs/orders-daily" },
  { workflow: "sales_marts", stage: "Transform", progress: "18 / 24 models complete", elapsed: "02:48", to: "/runs/orders-daily" },
  { workflow: "inventory_stream", stage: "Ingest", progress: "558 events · checkpoint open", elapsed: "00:36", to: "/runs/orders-daily" },
  { workflow: "events_backfill", stage: "Backfill", progress: "42 / 60 partitions complete", elapsed: "38:05", to: "/runs/orders-daily" },
];

export interface DataProductRow {
  name: string;
  freshness: string;
  quality: string;
  released: string;
  consumers: number;
  to: string;
}

export const dataProducts: Array<DataProductRow> = [
  { name: "Orders", freshness: "Fresh", quality: "1 failed", released: "08:00", consumers: 3, to: "/datasets/orders" },
  { name: "Customer profiles", freshness: "35m late", quality: "Passed", released: "08:00", consumers: 2, to: "/datasets/orders" },
  { name: "Product inventory", freshness: "Fresh", quality: "Unknown", released: "08:00", consumers: 4, to: "/datasets/orders" },
  { name: "Payments", freshness: "Fresh", quality: "Passed", released: "09:20", consumers: 2, to: "/datasets/orders" },
  { name: "Session events", freshness: "Fresh", quality: "3 warnings", released: "09:15", consumers: 1, to: "/datasets/orders" },
];

export interface QueueItem {
  name: string;
  state: string;
  detail: string;
}

export const releaseQueue: Array<QueueItem> = [
  { name: "inventory_daily", state: "Blocked", detail: "Missing validation · main unchanged" },
  { name: "product_catalog", state: "Ready", detail: "12 checks passed · awaiting promotion" },
];

/* ------------------------------------------------------------------- runs */

export interface RunStage {
  name: string;
  provider: string;
  outcome: string;
  /** Result bar offset/size as a percentage of the run timeline. */
  offset: number;
  width: number;
  duration: string;
  note?: string;
  flagged?: boolean;
}

export const runStages: Array<RunStage> = [
  { name: "Ingest orders", provider: "dlt", outcome: "Succeeded", offset: 0, width: 25, duration: "34s" },
  { name: "Build orders mart", provider: "dbt", outcome: "Succeeded", offset: 25, width: 54, duration: "1m 12s" },
  { name: "Validate candidate", provider: "Pandera", outcome: "Failed", offset: 79, width: 21, duration: "28s", flagged: true },
  { name: "Promote branch", provider: "Nessie", outcome: "Blocked", offset: 0, width: 0, duration: "—", note: "No provider mutation" },
  { name: "Deliver to target", provider: "Postgres", outcome: "Not started", offset: 0, width: 0, duration: "—", note: "No provider mutation" },
];

export interface DuplicateRow {
  orderId: string;
  recordId: string;
  createdAt: string;
  occurrences: number;
}

export const duplicateRows: Array<DuplicateRow> = [
  { orderId: "ORD-10482", recordId: "rec_7a8e01", createdAt: "2026-09-13 09:14:08", occurrences: 2 },
  { orderId: "ORD-10482", recordId: "rec_7a8e02", createdAt: "2026-09-13 09:14:09", occurrences: 2 },
  { orderId: "ORD-10517", recordId: "rec_7a8f14", createdAt: "2026-09-13 09:18:42", occurrences: 2 },
];

export interface RunEvent {
  time: string;
  level: "INFO" | "ERROR";
  message: string;
}

export const runEvents: Array<RunEvent> = [
  { time: "09:26:46", level: "INFO", message: "Candidate snapshot 938106 created on wap/r7e42b." },
  { time: "09:27:14", level: "ERROR", message: "unique_order_id failed: 42 rows. Release withheld." },
  { time: "09:27:14", level: "INFO", message: "Evidence complete. Released snapshot 938105 unchanged." },
];

/* ------------------------------------------------------------ run detail */

export const runMeta = {
  workflow: "orders_daily",
  runId: "r7e42b",
  status: "Failed validation",
  summary: "Run r7e42b · 13 Sep 2026, 09:25 UTC · Scheduled · Partition 2026-09-13",
  metrics: [
    { label: "Execution", value: "Failed", hint: "Blocking quality check", tone: "text-destructive" },
    { label: "Evidence", value: "Complete", hint: "All required stages recorded", tone: "text-success" },
    { label: "Release", value: "Not promoted", hint: "Consumers remain on 08:00", tone: "text-warning" },
    { label: "Duration", value: "2m 14s", hint: "09:25:00 – 09:27:14", tone: "text-foreground" },
    { label: "Attempt", value: "1 of 1", hint: "Previous success at 08:00", tone: "text-foreground" },
  ],
};

export const runDetails = [
  { label: "Asset", value: "marts.orders" },
  { label: "Orchestrator", value: "Dagster" },
  { label: "Trigger", value: "Schedule · orders_hourly" },
  { label: "Partition", value: "2026-09-13" },
  { label: "Code version", value: "a41c9f2" },
  { label: "Candidate snapshot", value: "938106" },
  { label: "Released snapshot", value: "938105" },
  { label: "Candidate branch", value: "wap/r7e42b" },
];

export const runConsumers = [
  { name: "Revenue dashboard", role: "Superset · Analytics" },
  { name: "Orders API", role: "PostgREST · Commerce" },
  { name: "Finance reconciliation", role: "dbt · Finance" },
];

export const runArtifacts = [
  "quality-results.json",
  "failed-rows.parquet",
  "dbt-run-results.json",
];

export const runLogLines = [
  { time: "09:25:00", level: "INFO" as const, message: "dlt pipeline orders_incremental start · partition 2026-09-13" },
  { time: "09:25:34", level: "INFO" as const, message: "ingest complete · 1,204,000 rows staged in 34s" },
  { time: "09:26:46", level: "INFO" as const, message: "candidate snapshot 938106 created on wap/r7e42b" },
  { time: "09:27:14", level: "ERROR" as const, message: "unique_order_id failed: 42 rows. Release withheld." },
  { time: "09:27:14", level: "INFO" as const, message: "evidence complete · released snapshot 938105 unchanged" },
  { time: "09:27:15", level: "INFO" as const, message: "evidence reconciled · terminal run" },
];

export const runSpans = [
  { name: "run.orders_daily", duration: "134,000ms", width: 100, tone: "bg-destructive" },
  { name: "stage.ingest_orders", duration: "34,000ms", width: 25, tone: "bg-success" },
  { name: "stage.build_mart", duration: "72,000ms", width: 54, tone: "bg-success" },
  { name: "stage.validate", duration: "28,000ms", width: 21, tone: "bg-destructive" },
];

export const runConfig = [
  { label: "Schedule", value: "orders_hourly · 25 * * * *" },
  { label: "Timeout", value: "30m per stage" },
  { label: "Retries", value: "0 · manual review required" },
  { label: "Quality gate", value: "Pandera · blocking" },
  { label: "Promotion", value: "Nessie branch wap/{run_id}" },
];

/* -------------------------------------------------------- dataset detail */

export const datasetMeta = {
  name: "Orders",
  status: "Published",
  summary: "marts.orders · Order-level revenue and fulfilment data for analytics and downstream APIs.",
  metrics: [
    { label: "Freshness", value: "Fresh", hint: "95m old · 2h freshness target", tone: "text-success" },
    { label: "Last released", value: "08:00 UTC", hint: "13 Sep 2026 · Run r6b190", tone: "text-foreground" },
    { label: "Released snapshot", value: "938105", hint: "Iceberg · Nessie main", tone: "text-foreground" },
    { label: "Released rows", value: "1,187,320", hint: "24.8 MB · 12 data files", tone: "text-foreground" },
    { label: "Released quality", value: "12 / 12 passed", hint: "Checks bound to snapshot 938105", tone: "text-success" },
  ],
};

export const datasetSchema = [
  { field: "order_id", type: "string", nullable: "No", role: "Primary key · Unique" },
  { field: "customer_id", type: "string", nullable: "No", role: "Customer reference" },
  { field: "created_at", type: "timestamp", nullable: "No", role: "Partition source" },
  { field: "status", type: "string", nullable: "No", role: "Accepted values" },
  { field: "order_total", type: "decimal(12,2)", nullable: "No", role: "Non-negative" },
  { field: "currency", type: "string", nullable: "No", role: "ISO 4217" },
  { field: "line_items", type: "json", nullable: "Yes", role: "Nested array" },
  { field: "updated_at", type: "timestamp", nullable: "Yes", role: "CDC watermark" },
];

export const datasetLineage = [
  { name: "postgres.orders", role: "Postgres · Source", direction: "Upstream" },
  { name: "stg_orders", role: "dbt · Model", direction: "Upstream" },
  { name: "marts.orders", role: "This dataset", direction: "Current" },
  { name: "Revenue dashboard", role: "Superset · Analytics", direction: "Downstream" },
  { name: "Orders API", role: "PostgREST · Commerce", direction: "Downstream" },
  { name: "Finance reconciliation", role: "dbt · Finance", direction: "Downstream" },
];

export const datasetPreview = [
  ["ORD-20913", "C-88142", "2026-09-13 08:59:01", "shipped", "412.90"],
  ["ORD-20914", "C-10293", "2026-09-13 08:59:02", "pending", "89.99"],
  ["ORD-20915", "C-55310", "2026-09-13 08:59:02", "shipped", "1204.00"],
  ["ORD-20916", "C-88142", "2026-09-13 08:59:03", "cancelled", "45.50"],
  ["ORD-20917", "C-29401", "2026-09-13 08:59:04", "shipped", "231.75"],
  ["ORD-20918", "C-77120", "2026-09-13 08:59:05", "pending", "99.00"],
];

export const datasetChecks = [
  { name: "order_id must be unique", outcome: "Failed · blocking", tone: "text-destructive" },
  { name: "order_total non-negative", outcome: "Passed", tone: "text-success" },
  { name: "currency in ISO 4217", outcome: "Passed", tone: "text-success" },
  { name: "status in accepted set", outcome: "Passed", tone: "text-success" },
  { name: "created_at not null", outcome: "Passed", tone: "text-success" },
  { name: "customer_id references dim_customer", outcome: "Warning", tone: "text-warning" },
];

export const datasetRuns = [
  { run: "r7e42b", finished: "09:27", outcome: "Failed validation", release: "Not promoted", duration: "2m 14s" },
  { run: "r6b190", finished: "08:00", outcome: "Succeeded", release: "938105", duration: "1m 58s" },
  { run: "r5d871", finished: "07:00", outcome: "Succeeded", release: "938104", duration: "2m 03s" },
];

export const datasetOwnership = [
  { label: "Owner", value: "Data platform" },
  { label: "Domain", value: "Commerce" },
  { label: "Freshness target", value: "2 hours" },
  { label: "Schedule", value: "Hourly" },
  { label: "Classification", value: "Internal" },
  { label: "Contract version", value: "v3 · Approved" },
];

export const datasetContract = [
  { label: "Freshness target", value: "2 hours" },
  { label: "Accepted statuses", value: "pending · shipped · cancelled" },
  { label: "PII handling", value: "customer_id tokenized downstream" },
  { label: "Retention", value: "7 years · cold after 1" },
  { label: "Breaking-change policy", value: "Minor versions only · 30-day notice" },
];

export const datasetAccess = [
  { label: "Serving target", value: "Postgres · marts.orders" },
  { label: "REST endpoint", value: "PostgREST · /orders" },
  { label: "Read access", value: "Analytics · Finance" },
  { label: "Service access", value: "Orders API" },
];

/* -------------------------------------------------------------- releases */

export const releaseMetrics = [
  { label: "Pending", value: "2 candidates", hint: "Across 2 catalog providers", tone: "text-foreground" },
  { label: "Ready for review", value: "1 candidate", hint: "All required evidence present", tone: "text-success" },
  { label: "Blocked", value: "1 candidate", hint: "Quality gate failed", tone: "text-destructive" },
  { label: "Released · 24h", value: "18 completed", hint: "Provider outcomes confirmed", tone: "text-foreground" },
  { label: "Unknown outcomes", value: "0 operations", hint: "No reconciliation needed", tone: "text-foreground" },
];

export const pendingCandidates = [
  {
    id: "rel-c204",
    dataset: "Customers",
    provider: "Polaris · Snapshots",
    readiness: "Ready for review",
    evidence: "Complete · 14/14 passed",
    created: "09:31",
    action: "Selected",
    selected: true,
  },
  {
    id: "rel-o193",
    dataset: "Orders",
    provider: "Nessie · Branch merge",
    readiness: "Blocked by quality",
    evidence: "Complete · 11/12 passed",
    created: "09:27",
    action: "Inspect",
    selected: false,
  },
];

export const candidateDetail = {
  id: "rel-c204",
  dataset: "Customers",
  subtitle: "Polaris snapshot publication · Run r8c291 · Data platform",
  status: "Ready for review",
  revision: "Release revision 42 → proposed 43",
  snapshotChanges: [
    { table: "crm.customers", released: "720114", candidate: "720128", delta: "+1,204" },
    { table: "crm.customer_segments", released: "881020", candidate: "881031", delta: "+312" },
  ],
  requiredEvidence: [
    { name: "Quality checks", detail: "14 passed · 0 blocking failures", outcome: "Passed" },
    { name: "Run evidence", detail: "All required stages and artifacts recorded", outcome: "Complete" },
    { name: "Snapshot audit", detail: "Both candidate snapshots match audit", outcome: "Matched" },
    { name: "Release revision", detail: "Expected 42 · Observed 42", outcome: "Current" },
  ],
  publicationPlan: [
    { label: "Operation", value: "Publish audited snapshots" },
    { label: "Catalog", value: "Polaris · analytics" },
    { label: "Expected revision", value: "42" },
    { label: "Intent", value: "Not submitted" },
  ],
};

export const latestReleases = [
  { id: "rel-s188", dataset: "Sessions", provider: "Nessie · Branch merge", ref: "main · 6fb812a", time: "09:12 UTC", outcome: "Merge confirmed" },
  { id: "rel-o187", dataset: "Orders", provider: "Nessie · Branch merge", ref: "main · 4a70d92 · Snapshot 938105", time: "08:00 UTC", outcome: "Merge confirmed" },
];

/* -------------------------------------------------------------- platform */

export const platformMetrics = [
  { label: "Enabled services", value: "12 of 16", hint: "4 discovered, not enabled", tone: "text-foreground" },
  { label: "Running", value: "12 of 12", hint: "Container state observed", tone: "text-foreground" },
  { label: "Ready", value: "11 of 12", hint: "1 readiness probe failing", tone: "text-warning" },
  { label: "Telemetry", value: "Partial", hint: "Loki log queries unavailable", tone: "text-warning" },
  { label: "Latest backup", value: "03:00 UTC", hint: "4/4 contributors complete", tone: "text-foreground" },
  { label: "Restore rehearsal", value: "Verified", hint: "11 Sep · Isolated target", tone: "text-success" },
];

export const platformServices = [
  { name: "Loki", role: "Log storage", runtime: "Running", readiness: "Not ready", probe: "Timeout · 5s", action: "Selected", attention: true },
  { name: "Dagster", role: "Orchestration", runtime: "Running", readiness: "Ready", probe: "HTTP 200", action: "Open" },
  { name: "Postgres", role: "Serving / state", runtime: "Running", readiness: "Ready", probe: "Connection OK", action: "Open" },
  { name: "MinIO", role: "Object storage", runtime: "Running", readiness: "Ready", probe: "HTTP 200", action: "Open" },
  { name: "Nessie", role: "Branch catalog", runtime: "Running", readiness: "Ready", probe: "HTTP 200", action: "Open" },
  { name: "Polaris", role: "REST catalog", runtime: "Running", readiness: "Ready", probe: "HTTP 200", action: "Open" },
  { name: "Trino", role: "Query engine", runtime: "Running", readiness: "Ready", probe: "HTTP 200", action: "Open" },
  { name: "Superset", role: "BI", runtime: "Running", readiness: "Ready", probe: "HTTP 200", action: "Open" },
  { name: "PostgREST", role: "Data API", runtime: "Running", readiness: "Ready", probe: "HTTP 200", action: "Open" },
  { name: "Traefik", role: "Routing", runtime: "Running", readiness: "Ready", probe: "HTTP 200", action: "Open" },
  { name: "OAuth2 Proxy", role: "Authentication", runtime: "Running", readiness: "Ready", probe: "HTTP 200", action: "Open" },
  { name: "Alloy", role: "Telemetry collector", runtime: "Running", readiness: "Ready", probe: "HTTP 200", action: "Open" },
];

export const backupCoverage = [
  { name: "Postgres · State & serving", outcome: "Complete" },
  { name: "MinIO · Object data", outcome: "Complete" },
  { name: "Nessie · Catalog state", outcome: "Complete" },
  { name: "Polaris · Catalog state", outcome: "Complete" },
];

export const maintenanceRows = [
  { name: "Table compaction", outcome: "6 / 6 completed" },
  { name: "Snapshot expiration", outcome: "Preview · 02:00 tomorrow" },
  { name: "Retention safeguard", outcome: "7 days · Pinned refs kept" },
  { name: "Restore rehearsal", outcome: "11 Sep · Verified" },
];

export const lokiDetail = [
  { label: "First failure", value: "09:28 UTC" },
  { label: "Last successful probe", value: "09:27 UTC" },
  { label: "Runtime source", value: "Container engine" },
  { label: "Readiness source", value: "HTTP /ready" },
];

export const dependencyPath = [
  { name: "Alloy → Loki", outcome: "Delivery unconfirmed", tone: "text-warning" },
  { name: "Loki → Log queries", outcome: "Unavailable", tone: "text-destructive" },
  { name: "Postgres → Run evidence", outcome: "Available", tone: "text-success" },
];

/* ------------------------------------------------------------ governance */

export const governanceMetrics = [
  { label: "Ownership", value: "124 / 128", hint: "4 datasets need an owner", tone: "text-foreground" },
  { label: "Classification", value: "126 / 128", hint: "2 datasets unclassified", tone: "text-foreground" },
  { label: "Contract coverage", value: "120 / 128", hint: "8 incomplete contracts", tone: "text-foreground" },
  { label: "Publication reviews", value: "3 pending", hint: "1 ready · 2 blocked", tone: "text-foreground" },
  { label: "Access verification", value: "1 drift detected", hint: "7 of 8 targets match policy", tone: "text-destructive" },
];

export const publicationReviews = [
  { dataset: "Shipments", owner: "Logistics", contract: "v2 · Complete", verdict: "Ready", reason: "All publication requirements met", action: "Selected", selected: true },
  { dataset: "Customer contacts", owner: "Unassigned", contract: "v1 · Complete", verdict: "Blocked", reason: "Accountable owner missing", action: "Inspect", blocked: true },
  { dataset: "Revenue forecast", owner: "Finance", contract: "v3 · Incomplete", verdict: "Blocked", reason: "Freshness expectation missing", action: "Inspect", blocked: true },
];

export const accessDrift = {
  title: "Access drift · Orders",
  verdict: "Unexpected UPDATE grant",
  subtitle: "Postgres · marts.orders · Role finance_reader · Verified at 09:32 UTC",
  rows: [
    { evidence: "Declared policy · v12", permissions: "SELECT", result: "Read only", drift: false },
    { evidence: "Compiled grants · v12", permissions: "SELECT", result: "Matches policy", drift: false },
    { evidence: "Verified backend grants", permissions: "SELECT, UPDATE", result: "Drift detected", drift: true },
  ],
};

export const ownershipGaps = [
  { dataset: "Pageviews", requirement: "Accountable owner", owner: "Unassigned", action: "Assign owner" },
  { dataset: "Support tickets", requirement: "Classification", owner: "Support", action: "Review classification" },
  { dataset: "Inventory balances", requirement: "Freshness expectation", owner: "Operations", action: "Review contract" },
];

export const publishShipments = [
  { label: "Dataset", value: "logistics.shipments" },
  { label: "Owner", value: "Logistics" },
  { label: "Classification", value: "Internal" },
  { label: "Contract", value: "v2 · Complete" },
  { label: "Policy verdict", value: "Requirements satisfied" },
  { label: "Transition", value: "Draft → Published" },
  { label: "Expected state version", value: "7" },
];

export const auditActivity = [
  { time: "09:32", actor: "Policy verifier", action: "Verify backend grants", target: "Orders · Postgres", outcome: "Drift recorded", tone: "text-warning" },
  { time: "09:21", actor: "data.steward", action: "Update contract · v1 → v2", target: "Shipments", outcome: "Applied · Version 7", tone: "text-success" },
  { time: "08:45", actor: "data.steward", action: "Publish Dataset", target: "Orders", outcome: "Published · Version 12", tone: "text-success" },
];

/* -------------------------------------------------------------- settings */

export const settingsMetrics = [
  { label: "Provider connections", value: "2 of 3", hint: "Polaris unreachable · evidence stale", tone: "text-foreground" },
  { label: "Unreachable", value: "1", hint: "Polaris · no response since 09:21", tone: "text-destructive" },
  { label: "Notification rules", value: "5", hint: "3 channels configured", tone: "text-warning" },
  { label: "Members", value: "14", hint: "4 operators · 3 reviewers", tone: "text-foreground" },
  { label: "Pending changes", value: "None", hint: "All settings applied", tone: "text-foreground" },
  { label: "Environment", value: "Production", hint: "Freshness default · 2h", tone: "text-success" },
];

export const providerConnections = [
  {
    name: "Polaris",
    role: "REST catalog · owns release evidence",
    endpoint: "polaris.phlo.internal/api",
    state: "Unreachable · timing out",
    detail: "Last confirmed response 09:21 · credential vault://prod/polaris valid",
    action: "Retry check",
    degraded: true,
  },
  {
    name: "Nessie",
    role: "Versioned catalog · owns commit history",
    endpoint: "nessie.phlo.internal:19120",
    state: "Connected · 42ms",
    detail: "Last check 09:35 · credential vault://prod/nessie valid",
    action: "Test connection",
    degraded: false,
  },
  {
    name: "Object storage",
    role: "S3-compatible · holds table files",
    endpoint: "s3://phlo-lake-prod",
    state: "Connected · 18ms",
    detail: "Last check 09:35 · credential instance role valid",
    action: "Test connection",
    degraded: false,
  },
];

export const degradedList = [
  "Release evidence and promotion status — shown as last confirmed",
  "Freshness on Polaris-served datasets — stamped, not live",
  "New promotions blocked — evidence cannot be re-verified",
];

export const unaffectedList = [
  "Released data — consumers keep reading snapshot 938105",
  "Execution — runs proceed; outcomes queue for reconcile",
  "Nessie-sourced history — commit trail stays live",
];
