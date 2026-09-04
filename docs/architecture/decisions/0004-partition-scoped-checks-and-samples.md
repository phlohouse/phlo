# 4. Make checks partition-aware and include failure sampling

Date: 2025-12-14

## Status

Accepted

## Context

Running checks against full tables can be:

- Expensive (especially on Iceberg tables).
- Misleading for partitioned pipelines (where correctness is evaluated per partition/run).

When checks fail, users also need actionable debugging context: a small sample and a query/SQL link
to reproduce the failure in Trino.

## Decision

Standardize two behaviors across check sources:

1. **Partition scoping**
   - If the run has a partition key, checks should scope to that partition by default.
   - When unpartitioned, checks may use a rolling window (configurable) unless explicitly full-table.
   - Default partition column is `_phlo_partition_date` unless overridden.
2. **Actionable failure output**
   - Include a small failure sample (<= 20 items) and a reproducible SQL snippet when possible.

## Consequences

- Checks become safe-by-default for partitioned assets (no accidental full-table scans).
- Observatory can show consistent failure drilldowns across check sources.
- Some sources (e.g., dbt) may not always provide row-level samples; the contract allows best-effort.
