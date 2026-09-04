# 27. Observatory performance monitoring and budgets

Date: 2025-12-20

## Status

Accepted

## Context

Observatory needs performance observability to ensure good user experience and diagnose issues. Currently there is no:

- Structured logging for server function calls
- API response time tracking
- Query execution timing
- Error tracking and reporting

This implements `phlo-954: Observatory: Performance monitoring and budgets`.

## Decision

### 1. Use Pino for Structured Logging

[Pino](https://github.com/pinojs/pino) is the standard Node.js structured logging library:

- 5-10x faster than Winston
- JSON-first output by default
- First-class TypeScript support
- Child loggers for adding context

#### Installation

```bash
cd packages/phlo-observatory/src/phlo_observatory && npm install pino
```

#### Logger Setup

Create `server/logger.server.ts`:

```typescript
import pino from "pino";

export const logger = pino({
  level: process.env.LOG_LEVEL ?? "info",
  formatters: {
    level: (label) => ({ level: label }),
  },
});

// Create child logger with function context
export function fnLogger(fn: string, meta?: Record<string, unknown>) {
  return logger.child({ fn, ...meta });
}

// Timing wrapper for async operations
export async function withTiming<T>(
  fn: string,
  operation: () => Promise<T>,
  meta?: Record<string, unknown>,
): Promise<T> {
  const log = fnLogger(fn, meta);
  const start = performance.now();

  try {
    const result = await operation();
    const durationMs = Math.round(performance.now() - start);
    log.info({ durationMs }, "completed");
    return result;
  } catch (error) {
    const durationMs = Math.round(performance.now() - start);
    log.error({ durationMs, err: error }, "failed");
    throw error;
  }
}
```

#### Example Usage

```typescript
// In trino.server.ts
import { withTiming } from "./logger.server";

export const previewData = createServerFn()
  .middleware([authMiddleware])
  .handler(async ({ data }) => {
    return withTiming(
      "previewData",
      () => {
        // existing logic
      },
      { table: data.table, branch: data.branch },
    );
  });
```

### 2. Server Function Instrumentation

Wrap key server functions with timing:

| Module              | Functions                                           | Why                   |
| ------------------- | --------------------------------------------------- | --------------------- |
| `trino.server.ts`   | `previewData`, `executeQuery`, `profileColumn`      | Query performance     |
| `dagster.server.ts` | `getHealthMetrics`, `listAssets`, `getAssetDetails` | Dagster API latency   |
| `iceberg.server.ts` | `listIcebergTables`, `getTableSchema`               | Metadata loading      |
| `nessie.server.ts`  | `getBranches`, `getBranchCommits`                   | Branch operations     |
| `quality.server.ts` | `getQualityChecks`                                  | Check results loading |

### 3. Performance Budgets

Log warnings when operations exceed acceptable thresholds:

| Operation               | Budget  | Rationale                  |
| ----------------------- | ------- | -------------------------- |
| Data preview (100 rows) | 2000ms  | User expects quick preview |
| Query execution         | 30000ms | Configurable timeout       |
| Table list              | 1000ms  | Sidebar should load fast   |
| Asset list              | 2000ms  | Hub page load              |
| Health metrics          | 1500ms  | Dashboard responsiveness   |

```typescript
export function withTimingBudget<T>(
  fn: string,
  budgetMs: number,
  operation: () => Promise<T>,
  meta?: Record<string, unknown>,
): Promise<T> {
  const log = fnLogger(fn, meta);
  const start = performance.now();

  return operation().then((result) => {
    const durationMs = Math.round(performance.now() - start);
    if (durationMs > budgetMs) {
      log.warn({ durationMs, budgetMs }, "exceeded budget");
    } else {
      log.info({ durationMs }, "completed");
    }
    return result;
  });
}
```

### 4. Log Output Examples

```json
{"level":"info","time":1734740000000,"fn":"previewData","table":"gold.fct_events","durationMs":234,"msg":"completed"}
{"level":"warn","time":1734740001000,"fn":"listAssets","durationMs":2500,"budgetMs":2000,"msg":"exceeded budget"}
{"level":"error","time":1734740002000,"fn":"executeQuery","durationMs":5234,"err":"Trino connection refused","msg":"failed"}
```

### 5. Implementation Summary

| File                       | Changes                                     |
| -------------------------- | ------------------------------------------- |
| `package.json`             | Add `pino` dependency                       |
| `server/logger.server.ts`  | [NEW] Pino logger setup with timing helpers |
| `server/trino.server.ts`   | Wrap key functions with `withTiming`        |
| `server/dagster.server.ts` | Wrap key functions with `withTiming`        |
| `server/iceberg.server.ts` | Wrap key functions with `withTiming`        |
| `server/nessie.server.ts`  | Wrap key functions with `withTiming`        |
| `server/quality.server.ts` | Wrap key functions with `withTiming`        |

## Consequences

### Positive

- Industry-standard logging library (Pino)
- Structured JSON logs enable aggregation and querying
- Timing data helps identify slow queries
- Child loggers add context without repetition
- Very low overhead

### Negative

- Adds one dependency (pino)
- Log volume increases
- No persistent metrics storage (logs only)

### Future Enhancements

- Add `pino-pretty` for dev-friendly output
- Aggregated metrics endpoint for Observatory UI
- Integration with external monitoring (Datadog, etc.)

## Verification Plan

### Automated Tests

1. **Existing tests still pass**:
   ```bash
   cd packages/phlo-observatory/src/phlo_observatory && npm test
   ```

### Manual Verification

1. **Check structured logs appear**:
   - Start Observatory: `cd packages/phlo-observatory/src/phlo_observatory && npm run dev -- --port 3001`
   - Open browser to http://localhost:3001
   - Navigate to Data Explorer, select a table
   - Check terminal output for JSON logs with `fn`, `durationMs` fields

2. **TypeScript/Lint checks**:
   ```bash
   cd packages/phlo-observatory/src/phlo_observatory && npm run check
   ```

## Beads

- phlo-954: Observatory: Performance monitoring and budgets (in_progress)
