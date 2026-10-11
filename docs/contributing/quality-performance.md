# Quality validation performance

This record covers the remaining algorithms in [issue #988](https://github.com/phlohouse/phlo/issues/988).
The baseline is [main at adf06946](https://github.com/phlohouse/phlo/commit/adf06946d4a8bbf4da2e5b1629710f5ba0e489c4).
It already contains #1095's read-once `StagedFrameLoader`; this change does not implement read-once again.
The measured implementation is [d90661a1](https://github.com/phlohouse/phlo/commit/d90661a1cb7507cf372a9249ab0d5a6792642082).

## Validation modes

`phlo_ingestion` and `DltIngester` accept `validation_mode`, `validation_batch_size`, and `validation_sample_size`.
The Parquet contract adapters in `phlo_dlt.pandera_checks` and `phlo_pandera.pandera_asset_checks` accept `mode`, `batch_size`, and `sample_size`.
The defaults are `materialized`, 65,536 rows, and 1,000 rows respectively.

| Mode | Scope | Memory and compatibility |
| --- | --- | --- |
| `materialized` | Entire staged dataset | Existing behaviour. Supports global contracts and preserves the existing error summary. |
| `full` | Every row, in bounded Parquet batches | Row-local contracts only. Carries global row offsets across files and accumulates counts with a 20-case failure sample. |
| `sample` | First `sample_size` rows across ordered files | Deterministic prefix, not a random or representative sample. Does not establish whole-dataset validity or uniqueness. |

Ingestion check metadata marks non-default modes with `validation_mode` and `validation_scope`.
All batch and sample sizes must be positive.
Full mode rejects dataset-level checks, uniqueness, indexes, parsers, regex columns, grouped checks, and checks with per-check failure limits.
Column checks must declare element-wise or row-local built-in behaviour.
The exception directs callers to `materialized` mode rather than silently weakening their contract.

Full-mode compatibility is incomplete. Structural schema errors can be counted once per batch rather than once per dataset.
The failure sample is batch-major rather than whole-dataset check-major, and the error text describes the first failing batch.
The query-backed `SchemaCheck` path still materialises its result; these validation modes apply to the Parquet adapters.
Arbitrary domain callbacks retain the shipped read-once frame because their interface accepts a complete DataFrame.
These limits leave #988's unrestricted unchanged-results acceptance criterion unmet.

## SQL aggregates and indexed reconciliation

Exact built-in `CountCheck`, `NullCheck`, `UniqueCheck`, and `RangeCheck` types execute SQL aggregates on DuckDB or Trino.
They reuse the DataFrame path's threshold, metric, and message construction.
Only schema metadata, scalar aggregates, and at most 20 failure rows enter Python.
Uniqueness counts every row in a duplicate group, including the first occurrence.
Floating-point NaN and SQL NULL follow the loaded numeric DataFrame's missing-value semantics.
Custom check types and subclasses retain one lazily loaded complete frame.

Failure row numbers describe query order, not persistent record identities.
Queries without an order have no stable distributed row order.
These checks do not provide snapshot isolation across their separate SQL statements.

Grouped quality reconciliation aligns source aggregates with a tuple index and compares column arrays.
Duplicate target groups and row-major mismatch samples remain intact.
Durable run-evidence reconciliation separately indexes stages and event statuses using #1018's typed input contract.
Its empty stage-ID regression preserves single-stage attribution and multi-stage rejection.

Iceberg detects duplicate keys with Arrow uniqueness rather than Python lists and sets.
The aligned merge keys convert to Python once before additive-schema retries, retaining the existing 1,000-key delete batches.
This remains proportional to the number of distinct merge keys; it is not a bounded-memory merge implementation.

## Million-row measurements

`scripts/benchmarks/quality_algorithms.py` creates identical deterministic Parquet files for both checkouts.
The default fixture has 1,000,000 rows, 34 numeric columns, ten files, 500,000 distinct keys, and one late range failure.
Grouped reconciliation uses 100,000 source groups, repeated target groups, and one deliberate final mismatch.
Each subprocess asserts independent row counts, failure positions, duplicate counts, or the distinct-key checksum.
It also checks imported module paths to prevent measuring the same editable installation twice.

Each measurement uses a fresh Linux process. Wall time excludes fixture generation and module imports.
Peak RSS includes imports and the measured operation; it is the process-wide `ru_maxrss`, not a memory delta.
DuckDB uses one thread and a 256 MiB engine memory limit in both variants.
The merge-key scenario exercises the real conversion and delete-expression code with a no-I/O transaction.
It does not measure provider commits, network calls, or object-store latency.

Environment: Python 3.12.13, Linux x86-64, pandas 3.0.6, Pandera 0.30.1, PyArrow 23.0.1,
DuckDB 1.5.1, and PyIceberg 0.11.1.
The table reports medians of three runs. Raw records are in [the million-row results](https://github.com/phlohouse/phlo/blob/9ad12a4a9653a91b6033b9e66b04d809c3ba6cf6/docs/contributing/quality-performance-1m.json).

| Scenario | Before seconds | After seconds | Before peak MiB | After peak MiB |
| --- | ---: | ---: | ---: | ---: |
| Row-local contract, materialised → full batches | 0.834 | 0.458 | 1504.8 | 303.3 |
| SQL built-in checks | 1.304 | 0.322 | 1251.8 | 205.1 |
| Grouped quality reconciliation | 22.234 | 1.854 | 214.2 | 307.4 |
| Iceberg merge-key preparation and expressions | 2.182 | 1.987 | 235.1 | 235.8 |
| Shipped read-once control, three domain callbacks | 0.543 | 0.517 | 770.5 | 771.2 |

The read-once control is unchanged; its timing difference is not a new optimisation.
Indexed reconciliation is about 12 times faster, but the index increases RSS by about 43% on this fixture.
The key-preparation improvement does not produce a material peak-memory gain in this no-I/O scenario.

A separate 2,000,000-row run keeps the batch size at 65,536.
Materialised validation takes 2.626 seconds and peaks at 2780.8 MiB.
Full batches take 1.006 seconds and peak at 333.3 MiB, versus 303.3 MiB at one million rows.
The algorithm retains batch-sized row data, but these two measured points are not a claim that all process memory is constant.
Raw records are in [the scaling results](https://github.com/phlohouse/phlo/blob/9ad12a4a9653a91b6033b9e66b04d809c3ba6cf6/docs/contributing/quality-performance-scaling.json).

### Independent rerun

A second checkout independently ran the exact published implementation against a pristine baseline.
All output assertions and module-provenance checks passed, and the million-row fixture checksum matched exactly.
The independent run used three repeats per million-row scenario and one two-million-row validation run.

| Scenario | Before seconds | After seconds | Before peak MiB | After peak MiB |
| --- | ---: | ---: | ---: | ---: |
| Row-local validation | 0.841 | 0.381 | 1513.4 | 317.3 |
| SQL built-in checks | 1.296 | 0.382 | 1261.5 | 204.7 |
| Grouped quality reconciliation | 21.787 | 2.001 | 213.9 | 307.3 |
| Iceberg key preparation | 2.336 | 1.943 | 234.9 | 236.7 |
| Shipped read-once control | 0.386 | 0.355 | 780.7 | 779.9 |

At two million rows, independent validation took 1.489 → 0.827 seconds and peaked at 2796.2 → 328.9 MiB.
The rerun reproduced the grouped-reconciliation memory increase and the absence of a merge-key RSS gain.
The timing differences do not support exact speed guarantees across environments.

### Reproduction commands

The locked development environment is bootstrapped with `make setup`.
These commands create a detached baseline and run the benchmark without installing baseline packages over the current checkout:

```bash
git worktree add --detach /tmp/phlo-quality-baseline adf06946d4a8bbf4da2e5b1629710f5ba0e489c4
uv run --locked python scripts/benchmarks/quality_algorithms.py \
	--baseline /tmp/phlo-quality-baseline --output /tmp/quality-1m.json
uv run --locked python scripts/benchmarks/quality_algorithms.py \
	--baseline /tmp/phlo-quality-baseline --rows 2000000 \
	--scenarios validation --repeats 1 --output /tmp/quality-scaling.json
git worktree remove /tmp/phlo-quality-baseline
```

## Complexity measurements

Ruff 0.15.9 reports C901 scores; radon 6.0.1 uses a different counting convention.
All six owned functions meet the C901 limit of 15 and have no C901 exemption.
The comparison uses the same baseline and measured implementation as the benchmark.

| Function | C901 before | C901 after | radon before | radon after |
| --- | ---: | ---: | ---: | ---: |
| `phlo_ingestion` | 31 | 14 | 5 | 1 |
| nested `decorator` | 28 | 13 | 13 | 13 |
| nested `run` | 25 | 10 | 54 | 24 |
| `merge_to_table_store` | 29 | 15 | 22 | 22 |
| `DltIngester.run_ingestion` | 27 | 15 | 68 | 47 |
| `MultiAggregateConsistencyCheck.execute` | 21 | 13 | 33 | 23 |

The measurement commands are:

```bash
uv run --locked ruff check --select C901 --ignore-noqa \
	--config 'lint.mccabe.max-complexity=0' \
	packages/phlo-dlt/src/phlo_dlt/{decorator,dlt_helpers,executor}.py \
	packages/phlo-pandera/src/phlo_pandera/reconciliation.py
uvx --from radon==6.0.1 radon cc -s -j \
	packages/phlo-dlt/src/phlo_dlt/{decorator,dlt_helpers,executor}.py \
	packages/phlo-pandera/src/phlo_pandera/reconciliation.py
```

The threshold-zero Ruff command intentionally exits non-zero to print every score.
The ordinary repository complexity gate passes.

## Verification record

The final branch includes [refreshed main at 0b8685e6](https://github.com/phlohouse/phlo/commit/0b8685e66cb9a299fa1a3c62adf9cacfec0505be)
and the stable #1018 typed-evidence prerequisite. Published prerequisite history is unchanged.
The dependent pull request targets `feat/1018-typed-evidence-base` to exclude both prerequisites from its diff.

- Refreshed `make setup` and `make check` pass. The broad run has 6520 passing tests, four existing skips, and 232 deselected integration tests.
- The fresh focused run has 919 passing tests, two existing skips, and 12 integration tests deselected.
  It covers all three affected packages, run-evidence reconciliation, lineage settings, and the core dependency boundary.
- The new real Trino 483 aggregate test passes, including NaN, SQL NULL, infinite bounds, and NaN bounds.
- `scripts/run_integration.py` passes all eight required lanes, including real Iceberg writes, schema generation, and ingestion.
- Python type checking, changed-file Ruff checks, formatting, and whitespace checks pass.
- `make docs-build` passes, and the rendered measurement table is readable without clipping.
- Benchmark assertions pass again on the refreshed branch, including the million-row SQL scenario after the final non-finite-bound correction.

This record does not claim issue closure. The full-mode compatibility limits above remain acceptance gaps.
