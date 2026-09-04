# 16. Add Observatory settings and server-side query guardrails

Date: 2025-12-16

## Status

Accepted

## Context

Observatory needs to talk to multiple backends (Dagster GraphQL, Trino, Nessie) and to provide a
consistent data exploration experience across environments. Hardcoding these connection details (or
keeping them scattered across the UI) makes it hard to switch environments, set sensible defaults,
and keep behavior consistent across pages.

In addition, Observatory includes a SQL runner. Without enforced guardrails, it is too easy to run
DDL/DML, multi-statement input, or unbounded queries that are unsafe for shared environments.

This decision is tracked by beads `phlo-npw` and `phlo-8os` (closed).

## Decision

- Add a typed Settings model for Observatory covering:
  - Connection URLs (Dagster GraphQL, Trino, Nessie).
  - Default context (catalog/schema/branch).
  - Query defaults (limit/timeout).
  - Safety (read-only / SELECT-only mode).
  - UI preferences (e.g., theme/density/date format).
- Persist settings in `localStorage` with environment-provided defaults used as the initial values.
- Route all Observatory backend calls through the settings-aware clients so the configured endpoints
  are respected consistently.
- Enforce server-side query validation and safety limits for the SQL runner:
  - Allowlist read-only statements (e.g., `SELECT`, `SHOW`, `DESCRIBE`, `EXPLAIN`).
  - Reject DDL/DML and multi-statement input.
  - Apply default and maximum `LIMIT` behavior, plus a statement timeout.
  - Return structured error information so the UI can clearly distinguish blocked vs failed vs
    timed-out queries.

## Consequences

- Users can switch environments and preferences without code changes.
- Query execution becomes safer by default; dangerous statements are blocked with clear errors.
- A future migration path to server-side, multi-user settings storage remains available when
  authentication/identity is introduced.
