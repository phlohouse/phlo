# 21. Observatory stage diff view

Date: 2025-12-18

## Status

Proposed

## Context

The Data Journey feature in Observatory shows how rows flow through the pipeline from bronze → silver → gold. Currently, users can see the transformation SQL and contributing rows at each stage, but there's no visual way to understand **what changed** between stages.

This makes it harder to:

1. Debug data quality issues (why did a value change?)
2. Understand schema evolution (what columns were added/removed?)
3. Trace aggregation effects (how did 150 rows become 1?)

Related beads:

- `phlo-1p9`: Add visual diff view between pipeline stages (this decision)
- `phlo-ecv`: Show lineage confidence and transform type in UI (completed - provides confidence scoring)
- `phlo-ruz`: Data Journey UX Improvements (blocked on this)

## Decision

Implement a **Stage Diff** component that compares data between adjacent pipeline stages:

### 1. StageDiff Component

Create `components/data/StageDiff.tsx` with three diff views:

#### Column Diff

Shows schema changes between stages:

- **Added columns**: Columns in downstream but not upstream (green)
- **Removed columns**: Columns in upstream but not downstream (red)
- **Renamed columns**: Detected via SQL column mappings (yellow)
- **Transformed columns**: Columns with function applied (blue)

```tsx
<ColumnDiff
  upstreamColumns={["created_at", "user_id", "payload"]}
  downstreamColumns={["event_date", "user_id", "total_events"]}
  mappings={columnMappings}
/>
```

#### Value Diff (1:1 transforms only)

For non-aggregate transforms, show before/after values:

| Column     | Upstream             | Downstream | Change    |
| ---------- | -------------------- | ---------- | --------- |
| event_date | 2024-01-01T10:30:00Z | 2024-01-01 | DATE()    |
| user_id    | 123                  | 123        | unchanged |

#### Aggregate Explanation

For GROUP BY transforms, show aggregation summary:

```
Grouped by: [activity_date]
Aggregations: COUNT(*) → total_events, SUM(score) → total_score
Source rows: ~150 rows → 1 row
```

### 2. Server-Side Diff Computation

Add `getStageDiff` server function in `contributing.server.ts`:

```typescript
export interface StageDiffResult {
  transformType: TransformType;
  confidence: number;
  columns: {
    added: string[];
    removed: string[];
    renamed: Array<{ from: string; to: string; transformation?: string }>;
    unchanged: string[];
  };
  aggregation?: {
    groupBy: string[];
    aggregates: Array<{ expression: string; alias: string }>;
    estimatedSourceRows: number;
  };
  sampleValues?: Array<{
    column: string;
    upstream: Primitive;
    downstream: Primitive;
    transformation?: string;
  }>;
}
```

### 3. Integration with RowJourney

Add a "Compare with upstream" button in `NodeDetailPanel` that opens the diff view:

```tsx
<Button onClick={() => setDiffOpen(true)}>
  Compare with upstream
</Button>
<StageDiff
  open={diffOpen}
  upstreamAssetKey={selectedUpstream}
  downstreamAssetKey={assetKey}
  rowData={rowData}
  onClose={() => setDiffOpen(false)}
/>
```

### 4. File Changes

| File                             | Change                                                                     |
| -------------------------------- | -------------------------------------------------------------------------- |
| `components/data/StageDiff.tsx`  | [NEW] Main diff component with ColumnDiff, ValueDiff, AggregateExplanation |
| `server/diff.server.ts`          | [NEW] Server function for computing diffs                                  |
| `components/data/RowJourney.tsx` | Add StageDiff integration to NodeDetailPanel                               |
| `utils/sqlParser.ts`             | Minor: expose additional helpers if needed                                 |

## Consequences

### Positive

- Users understand schema evolution at a glance
- Debugging data issues is faster (see what transformation caused change)
- Aggregation effects are clearly communicated
- Integrates with existing confidence scoring from `phlo-ecv`

### Negative

- Additional server queries for diff computation (mitigate with caching)
- More complex NodeDetailPanel component

### Risks

- SQL parsing limitations may miss some transformations (show "unknown" gracefully)
- Value comparison requires fetching sample data from both stages (performance concern)

## Verification Plan

### Automated Tests

1. **Unit tests for diff computation** (`server/diff.server.test.ts`):
   ```bash
   cd packages/phlo-observatory/src/phlo_observatory && npm test -- diff.server.test
   ```

   - Test column diff: added, removed, renamed, unchanged
   - Test aggregate detection from SQL
   - Test transform type classification integration

### Manual Verification

1. **Start Observatory in dev mode**:

   ```bash
   cd packages/phlo-observatory/src/phlo_observatory && npm run dev -- --port 3001
   ```

2. **Navigate to Data Explorer**:
   - Open http://localhost:3001/data/main
   - Select a gold table (e.g., `fct_daily_github_metrics`)
   - Click a row to open the Row Journey view

3. **Verify Stage Diff**:
   - Click on an upstream node (e.g., `stg_github_events`)
   - Click "Compare with upstream" button
   - Verify column diff shows:
     - `event_date` as renamed from `created_at` (via DATE())
     - `total_events` as added (aggregate)
   - Verify aggregate explanation shows GROUP BY columns

## Beads

- phlo-1p9: Add visual diff view between pipeline stages (complete)
