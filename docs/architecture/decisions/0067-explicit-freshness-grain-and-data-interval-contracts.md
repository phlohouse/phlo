# ADR 0067: Explicit freshness, grain, reconciliation and data-interval contracts

## Status

**Accepted (back-filled 2026-10-30)**

- Original decision: October 2026
- Evidence: [#1197](https://github.com/phlohouse/phlo/pull/1197) (explicit event-time
  freshness), [#1198](https://github.com/phlohouse/phlo/pull/1198) (grain and aggregate
  reconciliation rules) and [#1199](https://github.com/phlohouse/phlo/pull/1199)
  (bounded data intervals), all merged 2026-10-26;
  [research assessment](../../research/ssp-vault-phlo-assessment.md)
- Decision owner: Phlo project maintainers

## Context

Phlo's quality and scheduling contracts described successful execution, not the
properties of the data. The [research assessment](../../research/ssp-vault-phlo-assessment.md)
behind the three pull requests recorded the baseline:

- `SLA` recorded a freshness threshold but not which timestamp it applied to, so
  successful ingestion counted as freshness. In the before-and-after demonstration,
  30-hour-old events passed ingestion-time freshness against a two-hour promise.
- Quality rules covered nulls, uniqueness, ranges and accepted values, but had no
  composite grain or cross-relation conservation check. In the demonstration, a
  grouped join inflated settled revenue from 41 to 52 while grain and range checks
  passed, and the Dataset published.
- Runtime routing carried a partition key but no standard start and end interval,
  so adjacent loads, late arrivals and historical replay had no bounded extraction
  semantics.

The assessment recommended improving the contracts workflow authors declare and
the guarantees providers implement, with examples demonstrating them afterwards.

## Decision

All three contracts are opt-in, provider-neutral declarations in core, executed by
providers. Existing declarations keep their behaviour; none needs a migration.

1. **Explicit event-time freshness.** `SLA` gains keyword-only `freshness_column`
   and an optional timezone-aware `freshness_reference_time`. An explicit promise
   requires a non-negative `freshness_hours`. It compiles to a neutral quality
   rule that Pandera executes. It fails when timestamps are missing, unusable or
   absent, and on empty input. The age boundary is inclusive. Legacy SLAs keep
   ingestion-time semantics.
2. **Dataset grain.** `grain(*columns)` requires non-null, jointly unique
   business identities, including composite keys. Missing columns or any null key
   component fail. An empty dataset passes.
3. **Aggregate reconciliation.** `reconcile_aggregate()` compares `sum`, `avg`,
   `min`, `max` or `count` over independently filtered source and target rows,
   within the declaration's partition scope, with an inclusive absolute tolerance.
   Empty or all-null scopes fail for `sum`, `avg`, `min` and `max`. Relations are
   resolved against the runtime connection's catalogue and ref, never a pinned
   catalogue, so source and target are both read from the same candidate ref.
4. **Results feed existing policy.** Freshness and constraint results use the
   existing quality evidence and Dataset severity and publication policy: a
   blocking failure keeps the Dataset in draft, and a warning policy publishes
   without changing the failed verdict.
5. **Bounded data intervals.** `phlo.capabilities.DataInterval` is a half-open
   `[start, end)` window of timezone-aware instants normalised to UTC.
   `RuntimeRouting` carries `data_interval` and `interval_mode`, which is
   `scheduled` or `replay`; unknown modes are rejected. The Dagster adapter
   translates time-window partitions, including 23- and 25-hour days, and selects
   replay for backfills or the `phlo/interval_mode=replay` tag. Scheduled runs
   subtract an optional lookback from the start; replay applies none.
6. **Interval ingestion is bounded and stateless.** DLT sources opt in with
   `interval_column` and an optional `interval_lookback`. Interval mode requires
   merge writes and a stable non-null key, applies the bounds before staging, and
   leaves the live incremental cursor unchanged. Ingestion fails before calling the
   source when no interval is available.

## Consequences

- Authors can state consumer promises that a pipeline can fail, rather than
  inferring them from run success.
- Freshness does not imply completeness and does not reject future timestamps.
- Interval mode does not provide atomicity between an external cursor and the
  table, a contiguous watermark, or source query pushdown. Authors must push the
  bounds into API or SQL queries themselves. Kafka checkpointing and legacy
  unbounded DLT state are unchanged.
- Dataset-policy tests show publication blocks, not physical Nessie promotion.
- To downgrade, remove the declarations first; a source revert does not undo data
  already published.

## Alternatives Considered

- **Reinterpreting existing `freshness_hours` values:** rejected; the assessment
  required existing declarations to keep their meaning.
- **Provider-specific constraint APIs:** rejected in favour of neutral
  `phlo.quality_rules` factories that any quality provider can translate.
- **A separate checkpoint store for intervals:** not adopted; intervals use
  stateless bounded incrementals and merge writes instead.

## Related

- [ADR 0001](0001-quality-check-contract.md), [ADR 0003](0003-quality-severity-policy.md) and
  [ADR 0004](0004-partition-scoped-checks-and-samples.md): the quality contracts these rules extend.
- [ADR 0051](0051-dataset-authority.md): Dataset publication policy.
- [Ingest bounded data intervals](../../guides/data-intervals.md).
