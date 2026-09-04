# 10. Emit Pandera and dbt results as Dagster asset checks

Date: 2025-12-14

## Status

Accepted

## Context

Dagster asset checks are the canonical mechanism to surface data quality in the platform.
We want Pandera and dbt results to be visible as asset checks so that:

- Dagster UI, sensors, and GraphQL can treat quality signals uniformly.
- Observatory can build a Quality Center on top of Dagster checks, not bespoke sources.

## Decision

- In ingestion, emit a partition-scoped `pandera_contract` asset check based on the Pandera schema
  and staged parquet data; fail the run by default on contract failures.
- In dbt transforms, parse `run_results.json` + `manifest.json` after dbt execution and emit one
  Dagster asset check per dbt test, attached to the tested asset key, following the shared naming,
  metadata, and severity policies.

## Consequences

- Quality becomes first-class in Dagster across ingestion and transform layers.
- Check emission adds overhead (artifact parsing, metadata shaping) but remains bounded and testable.
- Check producers must maintain correct asset key mapping; the dbt translator becomes critical.
