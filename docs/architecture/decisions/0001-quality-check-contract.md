# 1. Standardize quality check naming and metadata contract

Date: 2025-12-14

## Status

Accepted

## Context

Phlo produces quality signals from multiple sources (Pandera validation, dbt tests, and custom checks).
Observability surfaces (Dagster UI, GraphQL consumers, and Observatory) need a consistent way to:

- Identify checks across runs and assets.
- Render results without source-specific branching.
- Provide enough debugging context (partition, counts, repro SQL, sample).

Without a contract, each producer would emit bespoke metadata keys and naming, forcing consumers to
special-case every check source and making new check sources expensive to add.

## Decision

Adopt a small, shared contract for all asset checks:

- **Naming**
  - Pandera contract check name is `pandera_contract`.
  - dbt checks are named deterministically as `dbt__<test_type>__<target>` (sanitized for Dagster).
- **Required metadata keys**
  - `source` (`pandera|dbt|phlo`)
  - `partition_key` (when applicable)
  - `failed_count`
  - `total_count` (when available)
  - `query_or_sql` (when applicable)
  - `sample` (<= 20 items, when available)
  - Optional: `repro_sql` (safe SQL snippet for investigation)

## Consequences

- Consumers can render quality results consistently and add features (filtering, drilldown) without
  needing per-source logic.
- Check producers must follow the contract; deviations are treated as bugs.
- Some metadata is necessarily best-effort (e.g., `total_count` or `repro_sql`) depending on source.
