# 15. Standardize Observatory tables on TanStack Table (+ virtualization)

Date: 2025-12-14

## Status

Accepted

## Context

Observatory contains multiple “data grid” style surfaces (Data Preview, Query Results, and various
column/value tables) that need consistent support for:

- Column resizing and pinning (especially for key columns).
- Sorting and filtering.
- Keyboard navigation and accessibility.
- Responsive rendering with very large result sets (50k+ rows).

Ad-hoc table implementations (or one-off wrappers) increase maintenance cost and make it difficult
to add features without regressions across pages.

This decision is tracked by bead `phlo-4su` (closed).

## Decision

- Adopt TanStack Table (`@tanstack/react-table`) as the single table engine for Observatory UI.
- Use TanStack Virtual (`@tanstack/react-virtual`) for row virtualization where the UI renders
  client-side rows.
- Keep server-side pagination for truly large result sets; virtualization optimizes rendering, not
  query execution.
- Implement a shared table wrapper component used by Data Preview and Query Results, parameterized
  by:
  - Column definitions (including type-aware formatting).
  - Row data and a stable row id getter.
  - Feature toggles (sorting, filtering, pinning, resizing).
- Use shadcn/ui primitives for styling and interactions (menus, inputs, badges) and preserve
  existing cell renderers (timestamps, nulls, links).

## Consequences

- Table behavior is consistent across pages, enabling faster iteration and fewer regressions.
- Future features (inline contributing rows, table browser improvements) can reuse the shared table
  component rather than reimplementing grid behavior.
- The frontend gains additional dependencies (`@tanstack/react-table`, `@tanstack/react-virtual`).
