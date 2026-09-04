# 11. Observatory Quality Center sources from Dagster GraphQL with caching and drilldown

Date: 2025-12-14

## Status

Accepted

## Context

Observatory needs a unified view of quality across assets with:

- A dashboard summary (counts, recent activity).
- Filtering (by layer/status/type).
- Drilldown (metadata, sample, history) and links to investigate in SQL.

Dagster is the source of truth for asset checks, but GraphQL queries can be expensive if repeatedly
issued from the UI.

## Decision

- Implement server-side aggregation of Dagster asset checks via Dagster GraphQL:
  - Fetch check definitions per asset.
  - Fetch recent check executions per asset and compute “latest status” per check.
  - Provide a consolidated API for overview, failing checks, recent executions, and per-check history.
- Use a short-lived in-memory cache to avoid hammering Dagster on rapid UI refresh.
- Provide “Open in SQL” deep links by reusing check metadata (`repro_sql` / `query_or_sql`) and by
  enabling `?sql=` and `?tab=` URL parameters in the Data Explorer.

## Consequences

- Observatory stays Dagster-check-native and does not need to understand Pandera/dbt internals.
- Caching improves perceived performance but introduces brief staleness (acceptable for monitoring).
- Deep linking depends on stable URL and query-handling semantics in the Data Explorer.
