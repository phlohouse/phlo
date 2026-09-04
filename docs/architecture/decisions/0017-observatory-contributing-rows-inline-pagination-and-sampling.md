# 17. Add inline contributing rows with pagination and deterministic sampling

Date: 2025-12-16

## Status

Accepted

## Context

Observatory currently renders "contributing rows" as SQL only: from a selected downstream (often
gold/aggregate) row, the UI can generate a deterministic query to fetch upstream rows, but it
requires the user to switch into the SQL runner to see results.

For transformed tables with a stable row key (`_phlo_row_id`), contributing rows are typically
small and can be fetched directly. For aggregate rows, the contributing set can be very large, so
the product needs a clear, safe strategy for bounded exploration (pagination and deterministic
sampling) with an auditable SQL trail.

This decision is tracked by beads `phlo-rml` and `phlo-504` (closed).

## Decision

- Add a server endpoint that returns contributing rows for a (downstream row, upstream table) pair.
- Support two modes (auto-selected):
  - Entity mode: if `_phlo_row_id` exists downstream and upstream, fetch matching upstream rows.
  - Aggregate mode: derive safe predicates from partition/date and mapped dimensions, then return a
    deterministic pseudo-random ordering with pagination.
- Enforce guardrails on the endpoint:
  - Hard caps for `pageSize` and overall server timeouts for Trino calls.
  - Deterministic ordering for sampling/pagination so results are repeatable.
  - Return the effective SQL used for audit/debug.
- Extend the Row Journey UI to render contributing rows inline in a drawer/panel:
  - Paginated table view (using the shared TanStack table wrapper).
  - Clear communication of mode (entity vs aggregate) and page size caps.
  - Retain "open in SQL" as a fallback/debug action.

## Consequences

- Users can inspect contributing rows inline without context switching to the SQL runner.
- Aggregate contributing rows are bounded and repeatable via deterministic sampling and paging,
  avoiding accidental expensive queries.
- The API and UI patterns become a reusable building block for future "Quality Center → source
  rows" workflows.
