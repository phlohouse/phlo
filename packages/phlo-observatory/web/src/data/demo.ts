/**
 * Stage 1 dummy data standing in for future phlo-api responses; each export names its endpoint.
 */
// Stage 1 dummy data mimicking future phlo-api responses (Mission Control).
// Every export documents the endpoint it stands in for (see API_NEEDS.md).

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

export const kpis = [
  { label: "Data", value: "128 datasets", sub: "121 fresh · 4 late · 3 unknown" },
  { label: "Ingestion", value: "18 sources", sub: "16 current · 1 delayed · 1 idle" },
  { label: "Quality", value: "612 checks", sub: "608 passed · 3 warnings · 1 failed" },
  { label: "Execution", value: "156 runs", sub: "149 succeeded · 4 active · 3 failed" },
  { label: "Releases", value: "2 pending", sub: "1 ready · 1 evidence blocked" },
  { label: "Governance", value: "124 owned", sub: "4 unassigned · 2 reviews due" },
];

export const needsAttention = [
  { icon: "alert", tone: "#C43D3D", title: "Orders delivery blocked", sub: "42 duplicate IDs · Revenue dashboard + 2 consumers · 8m", action: "Inspect run", to: "/runs/orders-daily" },
  { icon: "clock", tone: "#C95814", title: "Customer profiles are 35m late", sub: "09:00 delivery missed · CRM sync · Data platform", action: "View dataset", to: "/datasets/orders" },
  { icon: "branch", tone: "#737373", title: "Inventory release lacks evidence", sub: "Write succeeded · Validation missing · Released data unchanged", action: "Review", to: "/releases" },
];

export const activeExecution = [
  { wf: "orders_incremental", stage: "Ingest", progress: "1.2m rows staged", elapsed: "04:12", to: "/runs/orders-daily" },
  { wf: "sales_marts", stage: "Transform", progress: "18 / 24 models complete", elapsed: "02:48", to: "/runs/orders-daily" },
  { wf: "inventory_stream", stage: "Ingest", progress: "558 events · checkpoint open", elapsed: "00:36", to: "/runs/orders-daily" },
  { wf: "events_backfill", stage: "Backfill", progress: "42 / 60 partitions complete", elapsed: "38:05", to: "/runs/orders-daily" },
];

export const dataProducts = [
  { name: "Orders", freshness: "Fresh", quality: "1 failed", released: "08:00", consumers: 3, to: "/datasets/orders" },
  { name: "Customer profiles", freshness: "35m late", quality: "Passed", released: "08:00", consumers: 2, to: "/datasets/customer-profiles" },
  { name: "Product inventory", freshness: "Fresh", quality: "Unknown", released: "08:00", consumers: 4, to: "/datasets/product-inventory" },
  { name: "Payments", freshness: "Fresh", quality: "Passed", released: "09:20", consumers: 2, to: "/datasets/payments" },
  { name: "Session events", freshness: "Fresh", quality: "3 warnings", released: "09:15", consumers: 1, to: "/datasets/session-events" },
];

export const releaseQueue = [
  { name: "inventory_daily", state: "Blocked", sub: "Missing validation · main unchanged" },
  { name: "product_catalog", state: "Ready", sub: "12 checks passed · awaiting promotion" },
];

export const alerts = [
  { sev: "danger", title: "Release blocked", sub: "rel-0193 held at the evidence gate — Pandera uniqueness", action: "Open release →", time: "09:31" },
  { sev: "danger", title: "Evidence degraded", sub: "Polaris unreachable — figures shown as last confirmed 09:21", action: "Inspect provider →", time: "09:22" },
  { sev: "danger", title: "Run failed", sub: "orders_daily r7e42b — uniqueness check on order_id", action: "Open run →", time: "09:14" },
  { sev: "accent", title: "Unknown outcome", sub: "op-7c41 applied but unconfirmed — reconcile, don't retry", action: "Reconcile →", time: "08:47" },
  { sev: "muted", title: "Digest delivered", sub: "Nightly summary sent to #phlo-ops — recorded in Audit", action: "", time: "08:02" },
];

export const envs = ["Production", "Staging", "Development"];

export const searchIndex = [
  { kind: "Dataset", title: "Orders", meta: "128 columns · Fresh" },
  { kind: "Dataset", title: "Customer profiles", meta: "35m late" },
  { kind: "Run", title: "orders_daily r7e42b", meta: "Failed validation" },
  { kind: "Run", title: "sales_marts", meta: "Transform · running" },
  { kind: "Release", title: "inventory_daily", meta: "Blocked · evidence" },
  { kind: "Service", title: "OTel collector", meta: "Telemetry · Delayed" },
  { kind: "Page", title: "Overview", meta: "System overview" },
  { kind: "Page", title: "Governance", meta: "Policy and publication" },
];
