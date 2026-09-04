# 28. Unified Logging and Observability

Date: 2025-12-21

## Status

Proposed

## Context

Phlo consists of multiple services:

- **Python** - Dagster definitions, DLT ingestion, dbt transforms
- **TypeScript** - Observatory UI and server functions
- **Infrastructure** - Trino, Nessie, Postgres (Docker containers)

Currently:

- Python uses Dagster's `context.log.*()` with structured `extra={}` fields
- TypeScript (Observatory) now uses Pino for structured JSON logging (ADR-0027)
- Infrastructure logs are native container output
- All logs are collected by **Grafana Alloy** to **Loki** with 30-day retention

**Goal**: Enable Observatory to display logs from all services, correlated to assets, runs, and quality checks.

## Decision

### 1. Correlation Fields Standard

All services must include these fields in logs for correlation:

| Field           | Description        | Example              |
| --------------- | ------------------ | -------------------- |
| `run_id`        | Dagster run ID     | `"abc123-def..."`    |
| `asset_key`     | Dagster asset key  | `"dlt_user_events"`  |
| `job_name`      | Dagster job name   | `"github_pipeline"`  |
| `partition_key` | Partition date     | `"2025-12-20"`       |
| `check_name`    | Quality check name | `"pandera/contract"` |

### 2. Python Logging (Dagster)

Continue using `context.log.*()` with consistent correlation fields:

```python
# In asset/op code
context.log.info(
    "Processing partition",
    extra={
        "asset_key": context.asset_key.to_string(),
        "partition_key": context.partition_key,
        "run_id": context.run_id,
    }
)
```

### 3. TypeScript Logging (Observatory)

Use `withTiming` wrapper (ADR-0027):

```typescript
return withTiming(
  "queryTrino",
  async () => {
    // logic
  },
  { catalog, run_id },
);
```

### 4. Loki Integration in Observatory

Create `loki.server.ts` to query logs:

```typescript
export async function queryLogs({
  runId?: string,
  assetKey?: string,
  job?: string,
  level?: 'info' | 'warn' | 'error',
  start: Date,
  end: Date,
}): Promise<LogEntry[]> {
  const query = buildLogQuery({ runId, assetKey, job, level })
  return await lokiClient.queryRange(query, start, end)
}
```

### 5. Log Viewer UI

Add "Logs" tab to asset detail view showing:

- Logs filtered by `asset_key`
- Time-scoped to materialization window
- Level filtering (info/warn/error)
- Link to Grafana for advanced queries

### 6. Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Dagster       │    │   Observatory   │    │  Infrastructure │
│   (Python)      │    │  (TypeScript)   │    │  (Trino, etc)   │
└────────┬────────┘    └────────┬────────┘    └────────┬────────┘
         │ JSON logs            │ Pino                 │ native
         ▼                      ▼                      ▼
┌──────────────────────────────────────────────────────────────────┐
│                         Grafana Alloy                             │
│          (Docker log collection + JSON parsing)                   │
└────────────────────────────┬─────────────────────────────────────┘
                             ▼
                   ┌─────────────────┐
                   │      Loki       │
                   │  (Log storage)  │
                   └────────┬────────┘
                            │ LogQL queries
                            ▼
                   ┌─────────────────┐
                   │   Observatory   │
                   │   (Log viewer)  │
                   └─────────────────┘
```

### 7. Implementation Phases

| Phase | Bead        | Description                              |
| ----- | ----------- | ---------------------------------------- |
| 1     | phlo-954 ✅ | Observatory Pino logging (complete)      |
| 2     | phlo-27f ✅ | Loki querying server functions           |
| 3     | phlo-rti ✅ | Log Viewer UI                            |
| 4     | phlo-6jk ✅ | Python correlation field standardization |

## Consequences

### Positive

- Unified log viewing in Observatory
- Logs correlated to assets, runs, quality checks
- No external SaaS dependency (uses existing Loki/Grafana)
- Debug failures by viewing related logs inline

### Negative

- Adds Loki querying complexity to Observatory
- Log volume increases with structured fields
- Requires discipline to include correlation fields

### Trade-offs

- Observatory UI vs Grafana for log viewing
  - Observatory: integrated, asset-aware
  - Grafana: advanced queries, dashboards
  - Decision: Basic viewing in Observatory, link to Grafana for power users

## Verification Plan

### Automated Tests

- Unit tests for `loki.server.ts`
- Integration test: emit log, query via Loki API

### Manual Verification

1. Materialize an asset
2. View asset in Observatory
3. Click "Logs" tab
4. See related logs with timestamps and levels

## Beads

- phlo-8d2: Epic: Unified Logging and Observability
- phlo-954: Observatory structured logging ✅ (closed)
- phlo-27f: Loki log querying ✅ (closed)
- phlo-rti: Log Viewer UI ✅ (closed)
- phlo-6jk: Python correlation standardization ✅ (closed)
