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

export const alerts: Alert[] = [
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

export const searchEntries: SearchEntry[] = [
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

export const services: ServiceHealth[] = [
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

export const summaryMetrics: SummaryMetric[] = [
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

export const attentionItems: AttentionItem[] = [
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

export const activeExecution: ExecutionRow[] = [
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

export const dataProducts: DataProductRow[] = [
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

export const releaseQueue: QueueItem[] = [
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

export const runStages: RunStage[] = [
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

export const duplicateRows: DuplicateRow[] = [
  { orderId: "ORD-10482", recordId: "rec_7a8e01", createdAt: "2026-09-13 09:14:08", occurrences: 2 },
  { orderId: "ORD-10482", recordId: "rec_7a8e02", createdAt: "2026-09-13 09:14:09", occurrences: 2 },
  { orderId: "ORD-10517", recordId: "rec_7a8f14", createdAt: "2026-09-13 09:18:42", occurrences: 2 },
];

export interface RunEvent {
  time: string;
  level: "INFO" | "ERROR";
  message: string;
}

export const runEvents: RunEvent[] = [
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
