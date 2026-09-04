# 20. Observatory table browser improvements

Date: 2025-12-17

## Status

Accepted

## Context

The current Table Browser sidebar in Observatory provides basic functionality for browsing Iceberg
tables organized by medallion layer (bronze/silver/gold/publish). However, as the number of tables
grows, several UX issues emerge:

1. **No virtualization**: All table rows are rendered in the DOM, causing performance degradation
   with 100+ tables.
2. **Basic search**: Only filters on table name; users cannot search by schema or layer.
3. **Limited visual feedback**: Selected table state is not visually highlighted; loading states
   for individual tables are not shown.
4. **No keyboard navigation**: Users must use mouse to navigate the table tree.
5. **No schema grouping option**: Tables are grouped only by layer, not by schema.

The implementation currently lives in two places:

- `TableBrowser.tsx`: Original component with internal data fetching (deprecated)
- `$branchName.tsx`: Layout route with `TableBrowserCached` inline component

This ADR proposes a unified, improved Table Browser component.

Related beads:

- `phlo-13a`: Observatory: Table browser improvements (this decision)
- `phlo-b1w`: Observatory: Metadata caching (completed - provides server-side caching)
- `phlo-4su`: Observatory: TanStack Table migration (completed - provides virtualization patterns)

## Decision

Implement the following improvements to the Table Browser:

### 1. Virtualized List Rendering

Use `@tanstack/react-virtual` (already installed) to virtualize the table list. Only visible
table items are rendered in the DOM.

```typescript
const parentRef = useRef<HTMLDivElement>(null);
const rowVirtualizer = useVirtualizer({
  count: filteredTables.length,
  getScrollElement: () => parentRef.current,
  estimateSize: () => 32, // px per row
  overscan: 10,
});
```

### 2. Enhanced Search with Fuzzy Matching

Extend search to match on:

- Table name (primary)
- Schema name
- Layer name
- Combined (e.g., "bronze events" matches `bronze.dlt_user_events`)

Use a simple scoring function rather than a library to avoid bundle bloat:

```typescript
function matchScore(table: IcebergTable, query: string): number {
  const q = query.toLowerCase();
  const name = table.name.toLowerCase();
  const schema = table.schema.toLowerCase();
  const layer = table.layer.toLowerCase();

  if (name === q) return 100; // Exact name match
  if (name.startsWith(q)) return 80; // Prefix match
  if (name.includes(q)) return 60; // Substring match
  if (schema.includes(q)) return 40; // Schema match
  if (layer.includes(q)) return 20; // Layer match
  return 0;
}
```

### 3. Keyboard Navigation

Add keyboard support:

- `↑`/`↓`: Navigate between tables
- `Enter`: Select focused table
- `Escape`: Clear search and focus search input
- `Arrow Right`/`Left`: Expand/collapse layer groups

Implement using React state for `focusedIndex` and `onKeyDown` handler on the container.

### 4. List Density (from existing Settings)

Use the existing `settings.ui.density` preference (`'comfortable' | 'compact'`) already available
in Observatory Settings (ADR-0016). The Table Browser reads this setting and adjusts row heights:

| Density     | Row Height | Source                                            |
| ----------- | ---------- | ------------------------------------------------- |
| Compact     | 28px       | `settings.ui.density === 'compact'`               |
| Comfortable | 36px       | `settings.ui.density === 'comfortable'` (default) |

```typescript
const { settings } = useObservatorySettings();
const rowHeight = settings.ui.density === "compact" ? 28 : 36;

const rowVirtualizer = useVirtualizer({
  count: filteredTables.length,
  getScrollElement: () => parentRef.current,
  estimateSize: () => rowHeight,
  overscan: 10,
});
```

**Note**: No new density UI is added to the Table Browser - users change density in Settings.

### 6. Visual Improvements

- Add highlight for selected table that persists during navigation
- Show loading skeleton when tables are loading
- Add empty state with clear messaging for:
  - No tables in catalog
  - No tables matching search
  - Connection error (with retry button)
- Add table count badge in header

### 7. Consolidate Components

Remove the duplicated `TableBrowser.tsx` component and keep only the cached version in the
layout route. Extract the improved component to `components/data/TableBrowserVirtualized.tsx`
for reusability.

## File Changes

| File                                          | Change                                                |
| --------------------------------------------- | ----------------------------------------------------- |
| `components/data/TableBrowserVirtualized.tsx` | [NEW] Virtualized table browser with all improvements |
| `routes/data/$branchName.tsx`                 | Use new `TableBrowserVirtualized` component           |
| `components/data/TableBrowser.tsx`            | [DELETE] Remove deprecated component                  |
| `hooks/useTableBrowserKeyboard.ts`            | [NEW] Keyboard navigation hook                        |

## Consequences

### Positive

- Smooth scrolling with 500+ tables (virtualization)
- Faster search with fuzzy matching across name/schema/layer
- Keyboard-accessible navigation
- Consistent selected table highlight
- Clear empty/error states improve debuggability

### Negative

- Slightly more complex component (keyboard state, virtualizer refs)
- Schema grouping toggle adds UI complexity

### Risks

- Virtualization edge cases with dynamic height items: mitigate with fixed row height
- Keyboard navigation conflicts with global shortcuts: mitigate by only handling when component
  is focused

## Verification Plan

### Automated Tests

- Unit tests for `matchScore` function
- Component tests for keyboard navigation

### Manual Verification

- Verify virtualization works with 100+ tables (scroll performance)
- Test search: "bronze", "events", "gold fct", schema name
- Test keyboard: arrow keys, Enter to select, Escape to clear
- Verify selected table stays highlighted after navigation

## Beads

- phlo-13a: Observatory: Table browser improvements (complete)
