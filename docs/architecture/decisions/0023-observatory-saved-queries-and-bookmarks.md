# 23. Observatory saved queries and bookmarks

Date: 2025-12-18

## Status

Proposed

## Context

Observatory currently stores user settings (connections, preferences) in localStorage, but lacks persistence for:

1. **Saved Queries**: Users cannot save SQL queries for reuse. Each time they navigate away, the query is lost.
2. **Saved Views**: The current table/filters/sort state is not persisted or shareable.
3. **Bookmarks**: No way to bookmark frequently accessed tables or queries.
4. **Shareable URLs**: URLs don't fully encode navigation state, making it hard to share specific views.

This limits productivity for power users who frequently run the same queries or access the same tables.

Related beads:

- `phlo-5rf`: Observatory: Bookmarks + shareable URLs
- `phlo-pyg`: Observatory: Saved queries & views

## Decision

We will implement server-side persistence for saved queries and bookmarks, with shareable URLs that encode navigation state.

### 1. Server-Side Storage

Use localStorage for MVP (consistent with existing `observatorySettings`). Add versioned schema with types:

```typescript
// lib/savedQueries.ts
interface SavedQuery {
  id: string; // ULID
  name: string;
  query: string;
  description?: string;
  tags?: string[];
  branch?: string; // Optional default branch
  createdAt: string; // ISO timestamp
  updatedAt: string;
}

interface SavedView {
  id: string; // ULID
  name: string;
  tableRef: {
    // Full table reference
    catalog: string;
    schema: string;
    table: string;
  };
  columns?: string[]; // Selected columns
  filters?: string; // WHERE clause fragment
  sortBy?: { column: string; direction: "asc" | "desc" };
  createdAt: string;
  updatedAt: string;
}

interface Bookmark {
  id: string;
  type: "table" | "query" | "view";
  targetId: string; // Table path or SavedQuery/SavedView ID
  label?: string; // Optional display name
  createdAt: string;
}
```

### 2. Shareable URLs

Encode navigation state in URL search params:

```
/data/main/gold/fct_metrics?cols=date,value&sort=date:desc&q=value>100
```

URL encoding:

| Param    | Description                  | Example                |
| -------- | ---------------------------- | ---------------------- |
| `cols`   | Selected columns (comma-sep) | `cols=id,name,value`   |
| `sort`   | Sort column:direction        | `sort=created_at:desc` |
| `q`      | Filter expression            | `q=status='active'`    |
| `limit`  | Row limit                    | `limit=50`             |
| `offset` | Pagination offset            | `offset=100`           |

For saved queries, use a short ID in the path:

```
/data/query/$queryId
```

### 3. New Files

| File                                    | Purpose                                                |
| --------------------------------------- | ------------------------------------------------------ |
| `lib/savedQueries.ts`                   | Schema + localStorage CRUD for queries/views/bookmarks |
| `hooks/useSavedQueries.ts`              | React hook for query persistence                       |
| `hooks/useBookmarks.ts`                 | React hook for bookmarks                               |
| `components/data/SavedQueriesPanel.tsx` | UI panel listing saved queries                         |
| `components/data/BookmarkButton.tsx`    | Bookmark toggle button                                 |
| `routes/data/query.$queryId.tsx`        | Route for loading saved query                          |

### 4. UI Components

**SavedQueriesPanel**: Collapsible sidebar section showing saved queries grouped by tags. Actions: Run, Edit, Delete, Copy SQL.

**BookmarkButton**: Star icon in table header and query editor. Click to bookmark/unbookmark.

**QueryEditor enhancements**:

- "Save Query" button opens dialog to name/tag the query
- Dropdown to load saved queries

### 5. URL State Sync

Use TanStack Router's `searchParams` to sync state:

```typescript
// In route component
const { cols, sort, q } = Route.useSearch();

// Update URL without navigation
navigate({ search: { cols: newCols, sort, q } });
```

## Consequences

### Positive

- Users can save frequently-used queries and access them quickly
- URLs are fully shareable - copy URL to share exact view with teammates
- Bookmarks provide quick access to important tables/queries
- Consistent with existing localStorage pattern (later upgradeable to server-side)

### Negative

- localStorage has size limits (~5MB) - sufficient for typical usage
- No cross-device sync until server-side storage is added
- URL complexity increases

### Risks

- URL encoding edge cases (special characters in filters) - mitigate with proper encoding
- localStorage data loss on browser clear - document in UI

## Verification Plan

### Automated Tests

1. **Unit tests for `savedQueries.ts`**:
   - Test CRUD operations (create, read, update, delete)
   - Test schema validation
   - Test localStorage serialization/deserialization

2. **URL encoding tests**:
   - Test encoding/decoding of complex filter expressions
   - Test handling of special characters

### Manual Verification

1. **Saved Queries Flow**:
   - Open Query Editor, write a query, click "Save Query"
   - Enter name and tags, confirm save
   - Refresh page, verify query appears in Saved Queries panel
   - Click saved query to load it, run to verify it executes

2. **Bookmarks Flow**:
   - Navigate to a table, click bookmark star
   - Verify bookmark appears in sidebar/bookmarks list
   - Click bookmark to navigate back to table

3. **Shareable URLs Flow**:
   - Select columns, apply sort, add filter
   - Copy URL from browser
   - Open in new tab/incognito, verify same state loads

## Beads

- phlo-5rf: Observatory: Bookmarks + shareable URLs (complete)
- phlo-pyg: Observatory: Saved queries & views (complete)
