# 18. Observatory command palette global search

Date: 2025-12-17

## Status

Accepted

## Context

Observatory needs a quick way to navigate across different entity types: Dagster assets, Iceberg tables,
table columns, and recent pipeline runs. Users should be able to find and jump to these items without
manually navigating through the sidebar hierarchy.

A basic `CommandPalette.tsx` component already exists with ⌘K/Ctrl+K support and limited asset search.
This decision extends it to support the full searchable entity index, caching, and additional actions.

This decision is tracked by bead `phlo-8aw`.

## Decision

- Preload and cache the search index at application init using existing server functions:
  - Assets from `getAssets` (Dagster GraphQL).
  - Tables and columns from `getTables` and `getTableSchema` (Iceberg via Trino).
- Use the existing `cmdk` library which already handles fuzzy search and keyboard navigation.
- Add debouncing (300ms) for column schema lookups to avoid overwhelming Trino on initial load.
- Extend the command palette with new search groups and actions:

  | Group         | Source                    | Action                                |
  | ------------- | ------------------------- | ------------------------------------- |
  | Assets        | `getAssets`               | Open Asset page, Focus in Graph       |
  | Tables        | `getTables`               | Open in Data Explorer                 |
  | Columns       | `getTableSchema` (cached) | Open table, copy column name          |
  | SQL Templates | derived from Tables       | Copy `SELECT * FROM ...` to clipboard |

- Store the search index in React state at the `__root.tsx` level, refreshing on route changes to
  the Data Explorer (to pick up new tables).
- "Insert SQL template" copies a `SELECT * FROM "catalog"."schema"."table" LIMIT 100` string to
  the clipboard and shows a toast notification.

## Consequences

- Users can quickly navigate to any asset, table, or column via ⌘K.
- The search index is cached and preloaded, so lookups are instant after initial load.
- Adding more entity types later (e.g., saved queries, bookmarks) follows the same pattern.
- Column schema lookups may add initial load latency; we mitigate this with progressive loading
  (tables first, columns after).
