# WAP Failure Lab

A runbook-style Phlo lakehouse that exists to answer one question: when a
Write-Audit-Publish run misbehaves - bad data, flaky sources, schema drift,
racing partitions - what exactly happens to the branch, the report in
`.phlo/wap-reports/`, and main?

Six scripted scenarios each drive `phlo materialize`, inspect the WAP report
(schema `phlo.wap_report.v2`), query Trino counts, and inspect Nessie refs.
Every intentional failure fixture breaks exactly one named invariant, and the
container-free pytest suite proves it.

The project owns its uv environment and deterministic fixtures. It does not
depend on another example's runtime state.

## What it exercises

| Area | Coverage |
|---|---|
| Ingestion | One core dataset (`sensor_batches`: batch_id, sensor_id, reading_value, recorded_at, batch_date, quality_flag) ingested by two assets from the same reader and contract: `dlt_sensor_batches` (strict/blocking) and `dlt_sensor_batches_relaxed` (`strict_validation=False`) |
| WAP branch lifecycle | Branch creation per launch, promotion merging to main, post-promotion cleanup, retained branches for failed runs, 24-hour retention cleanup |
| Quality | Pandera contract (not-null, bounds, flags) plus domain checks `assert_batch_ids_unique` and `assert_recordings_near_partition`; blocking versus warning semantics are the central lesson |
| Retry | Env-or-file armed one-shot source failure with a durable attempt counter file; asset declares `max_retries=3` |
| Schema change | Additive optional column `reading_quality_score`; old rows stay NULL, old readers unaffected |
| Concurrency | Back-to-back partition runs on distinct branches with disjoint ids |
| Transforms | One minimal dbt model `batch_summary` (count per sensor) proving downstream only ever sees promoted rows |
| Data plane | Blessed Iceberg stack (MinIO + Nessie + Trino), WAP branch promotion |

## Layout

```text
scripts/generate_fixtures.py    deterministic scenario batches under generated-data/scenarios/
scripts/run_scenario.py         drives one scenario end to end and asserts outcomes
scripts/inspect_branches.py     lists pipeline-run-* refs older than N minutes
scenarios/<name>/SCENARIO.md    steps + expected outcome per scenario
workflows/ingest/batches.py     strict + relaxed ingestion assets over the inbound staging dir
workflows/schemas/contracts.py  SensorBatchSchema incl. optional reading_quality_score
workflows/quality/validators.py domain checks following the quality_checks protocol
workflows/retry/transient.py    transient failure injection + attempt counter
workflows/schedules/lab.py      three stopped schedules
workflows/transforms/dbt/       single-model dbt project (batch_summary)
tests/                          fast deterministic container-free suite
```

The pipeline reads ONLY `generated-data/inbound/`. WAP launches execute inside
the Dagster service where CLI-side environment variables do not reach assets,
so `run_scenario.py` stages the chosen scenario's files into inbound before
each launch; `generate_fixtures.py` defaults inbound to the valid_publish
batch.

## Run the lakehouse

From this directory:

```bash
uv sync --locked --group dev
uv run python scripts/generate_fixtures.py
uv run pytest -q
uv run ruff check .
```

Start the platform:

```bash
uv run phlo services init --force --no-dev
uv run phlo services start --build
uv run phlo doctor
```

Run scenarios (in this order for a fresh catalog):

```bash
uv run python scripts/run_scenario.py valid_publish      # clean promote
uv run python scripts/run_scenario.py quality_failure    # fail-closed evidence
uv run python scripts/run_scenario.py retry_recovery     # fail once, recover
uv run python scripts/run_scenario.py schema_change      # additive migration
uv run python scripts/run_scenario.py concurrent_runs    # serial isolation
uv run python scripts/run_scenario.py warning_only       # non-blocking lesson
```

Inspect results:

```bash
uv run python scripts/inspect_branches.py --older-than-minutes 60
uv run phlo trino --execute 'SELECT count(*), count(DISTINCT batch_id) FROM iceberg.raw.sensor_batches'
uv run phlo catalog tables
```

## Expected results

Fixture counts (byte-stable across regenerations):

| Scenario file | Partition | Rows | Flavor |
|---|---|---|---|
| valid_publish/batches-2026-08-20 | 2026-08-20 | 12 | clean baseline (s-001..s-004 x3) |
| quality_failure/batches_null_reading | 2026-08-20 | 6 | b-1004 has null reading_value |
| quality_failure/batches_duplicate_batch_id | 2026-08-20 | 7 | b-2003 appears twice |
| retry_recovery/batches-2026-08-22 | 2026-08-22 | 10 | clean; failure injected at runtime |
| schema_change/batches-2026-08-23 | 2026-08-23 | 8 | carries reading_quality_score |
| concurrent_runs/partition_a | 2026-08-20 | 12 | s-101..s-104, b-6001..b-6012 |
| concurrent_runs/partition_b | 2026-08-21 | 8 | s-201..s-204, b-7001..b-7008 |
| warning_only/batches_stale-2026-08-24 | 2026-08-24 | 7 | recorded 23 days before partition |

Live outcomes asserted by `run_scenario.py`:

- valid_publish: report `promoted` with `target_hash_after != target_hash_before`;
  main gains exactly 12 rows; branch removed after promotion; rebuilding
  `batch_summary` yields batch_count 3 per sensor.
- quality_failure: Dagster run fails on the strict contract; main row count and
  ref hash unchanged; report retained with a `dagster_run_id`; violating branch
  still present for audit.
- retry_recovery: attempt counter file reads exactly `2`; report `promoted`;
  main gains exactly 10 rows; branch gone.
- schema_change: column count grows by exactly 1 with `reading_quality_score`
  present; every pre-change row has NULL score; partition gains 8 rows.
- concurrent_runs: both reports `promoted` on different branches; per-partition
  counts are multiples of 12 / 8; total delta 20 on a fresh catalog; both
  branches cleaned up.
- warning_only: relaxed run succeeds despite its failed check; the relaxed
  table gains 7 rows ON MAIN. Under the neutral severity contract (ADR 0048 /
  #817) the WARN-only failure is non-blocking, so the promotion sensor merges
  the branch and the report ends `promoted` with `passed_with_warnings`
  aggregate quality evidence (severity `warn`, not blocking). See
  scenarios/warning_only/SCENARIO.md - warnings are durable evidence, never a
  promotion gate.

## Known scenario quirks observed live

- `schema_change` reruns are idempotent at the data level (merge keeps 8
  distinct batch ids), but repeated executions of the same scenario were
  observed doubling physical rows once - under investigation upstream.
- `concurrent_runs` partition B occasionally misses its promotion report
  window under load; rerunning the scenario resolves it.
- warning_only (live-proven 2026-09-03): a failed WARN-severity check is
  durable, non-blocking evidence under the neutral severity contract. The run
  succeeds, the promotion sensor merges the WAP branch with
  `passed_with_warnings` aggregate evidence, and main advances - warnings are
  recorded, never fatal; only ERROR-severity failures block promotion.

## Expected failures

Each labeled fixture breaks exactly ONE invariant, proven by
tests/test_wap_failure_lab.py:

- `batches_null_reading`: null reading_value fails the blocking Pandera
  contract; uniqueness and staleness checks stay green.
- `batches_duplicate_batch_id`: b-2003 repeated fails
  `assert_batch_ids_unique`; contract and staleness stay green.
- `batches_stale`: recordings 23 days before their partition fail
  `assert_recordings_near_partition` (> 7-day window); contract passes -
  which is what makes it the warning_only fixture.
- The transient failure is not a fixture but an injected runtime fault
  (attempt 1 of an armed run raises once).

To reproduce the strict fail-closed path live:
`run_scenario.py quality_failure`.

## Schedules

Three schedules register with Dagster, all STOPPED so an example checkout never
launches work unexpectedly:

| Schedule | Cron | Job |
|---|---|---|
| hourly relaxed feed | `10 * * * *` | re-ingest staged batches without blocking checks |
| daily batch ingestion | `30 2 * * *` | strict ingest of the current inbound staging |
| weekly reconciliation | `0 4 * * 1` | full WAP pass over every asset |

Asset settings are justified by behavior: max_retries=3 with a 5-second delay
on the strict asset absorbs transient source outages (retry_recovery); append
merge keeps raw evidence reproducible per run; identity partitioning by
batch_date gives exact per-partition count assertions.

## Profile maturity

Blessed Iceberg stack (MinIO + Nessie + Trino). CI-first: pytest needs no
containers because reports, validators, retries, and concurrency are exercised
as pure functions against synthetic payloads copied from the real report
schema.

## Platform requirements and known semantics

Requires the standard phlo platform images matching the other examples.
Temporal contract fields are typed natively (DLT normalizes ISO-8601 during
staging); batch_date becomes a timestamptz daily identity partition.

### Platform gaps observed (for release notes)

1. **Failed Dagster runs terminalize (live-proven 2026-09-03).** The
   auto-promotion sensor scans SUCCESS, FAILURE, and CANCELED runs and
   transitions a failed run's WAP report to `status="failed"` with
   `failure_reason="dagster_run_failed"`, retaining the branch and query
   catalog for audit until the cleanup sensor's retention policy applies.
   Verified live against the blessed stack: the quality_failure run reached
   Dagster `FAILURE` and its report terminalized as
   `failed`/`dagster_run_failed` with main untouched (`failure_reason` values
   that exist today: `dagster_run_failed`, `asset_checks_failed`,
   `quality_evidence_unavailable`, `launch_manifest_or_immutable_tags_invalid`,
   `merge_branch_returned_false`, `branch_cleanup_incomplete`).
2. **pyiceberg's REST catalog exposes no reference enumeration.**
   `phlo_iceberg.get_catalog()` (pyiceberg 0.12.0rc1 RestCatalog) offers no way
   to list branches or read hashes, so ref inspection uses
   `phlo_nessie.resource.NessieResource` (`list_branches`, `get_branch_hash`) -
   the same client the platform sensors use.
3. **WAP launches bypass CLI environment variables.** `phlo materialize --wap`
   submits the run to the Dagster service via GraphQL; assets execute there, so
   scenario routing and retry arming use files on the shared project filesystem
   (`generated-data/inbound/`, `.phlo/wap-lab/*`) with env overrides honored
   only for in-process runs.
4. **Non-blocking checks never gate promotion (live-proven 2026-09-03).**
   Under the neutral severity contract (#817) a failed WARN-severity check is
   non-blocking: `_all_checks_passed` treats only ERROR-severity failures as
   blocking, the successful run promotes with `passed_with_warnings` aggregate
   quality evidence (severity `warn`, `blocking=false`), and the report ends
   `promoted` with no failure reason. Verified live: the warning_only run
   promoted, main gained exactly 7 rows, and the durable aggregate quality
   result recorded `passed=true, severity=warn, blocking=false`.
5. Live scenario execution was completed 2026-09-03 against the Docker stack
   (blessed MinIO + Nessie + Trino + Dagster). Five of the six scenarios were
   run end to end for the live WAP proof (issue #832): valid_publish,
   warning_only, quality_failure, an evidence-sink outage/recovery case, and
   retry_recovery; schema_change and concurrent_runs remain asserted by the
   container-free suite only.
