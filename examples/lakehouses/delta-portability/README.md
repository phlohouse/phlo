# Delta Portability lakehouse

The Delta Lake twin of the IoT telemetry example: identical pipeline shape,
identical fixture arithmetic discipline, and provider-neutral workflow code -
with every table routed to `table_store: delta` instead of the blessed
Iceberg stack. It exists to answer one question: how much of a Phlo lakehouse
survives when the only thing that changes is the table store - and which
guarantees are Iceberg-specific?

The project owns its uv environment, deterministic fixtures, workflow
configuration, and a local device registry database. It does not depend on
another example's runtime state.

## What it exercises

| Area | Coverage |
|---|---|
| Ingestion | `phlo.ingest.dlt` assets appending gzip-compressed NDJSON hourly files into Delta, merging late correction batches by `message_id`, merging two reference tables out of a SQLite registry database, and merging a Sling-replicated regions lookup |
| Replication | One Sling stream (`public.regions`) in full-refresh mode from the compose PostgreSQL source |
| Schema evolution | Additive optional contract column (`signal_quality_dbm`) planned through phlo's migration planner and applied to a populated Delta table without touching existing rows |
| Time travel | Version history inspection, idempotent merges across replays, and restore-based recovery from a bad delivery |
| Maintenance | Compaction and vacuum through the provider API plus maintenance recommendations |
| Transforms | Pipeline-stage dbt models through one project with stage-scoped model paths: deduplication plus correction overlay, hourly health and daily fleet summaries, consumer-facing current health and site report enriched with replicated regions |
| Quality | Physical bounds via blocking Pandera contracts at ingest; sequence monotonicity, duplicate ratio, partition-integrity, and single-correction validators following the `quality_checks` protocol; labeled failure fixtures per invariant |
| Orchestration | Hourly ingestion, rolling late-data repair, daily reference refresh, weekly reconciliation; all schedules stopped by default |
| Partitions | Raw telemetry is identity-partitioned by `event_date` - Delta supports identity-only partition transforms, unlike the Iceberg hourly transform used by the sibling |
| Data plane | Delta Lake tables via delta-rs (`phlo-delta`), no versioned catalog, **no WAP** |

## Provider reality

Everything below was verified against this repository's sources and the real
deltalake engine; nothing is aspirational.

Routing and support surface:

- `phlo.yaml` sets `capabilities.defaults.table_store: delta`, and every
  ingestion asset pins it explicitly with
  `capabilities={"table_store": "delta"}`. Porting back to Iceberg is flipping
  those values and dropping `phlo-delta` from dependencies.
- `phlo-delta` registers `table_store:delta` and `schema_migrator:delta`.
  Its declared support is `supports_refs=false`, identity-only partition
  transforms, snapshots/compaction/vacuum/time-travel supported.
- Merge is natively supported (delta-rs MERGE): applying corrections twice
  inserts nothing new (proven by tests, see below).
- WAP semantics do not exist on this data plane. Branch-first launches need a
  versioned catalog; Delta has none here, `supports_refs` is false, and
  `wap.enabled` stays `false`. Recovery from a bad delivery is a restore to
  the last good version instead of branch discard - both fail closed at the
  validation boundary, but Delta loses the pre-publish isolation window.

Verified platform gaps (recorded for release notes):

1. **Dev-stack Trino cannot query Delta.** `packages/phlo-trino` ships only
   an `iceberg` catalog; there is no delta catalog in the default compose
   stack (the only delta catalog properties live inside phlo-delta's own
   integration test compose, mounted onto a stock Trino image). The dbt
   profile therefore points at `catalog: delta` with a comment: models
   compile but require an operator-provisioned catalog such as
   `connector.name=delta_lake` with a file metastore under the warehouse
   path. Without Trino, inspect Delta tables directly:
   `uv run python scripts/delta_history.py` (provider API) or any
   deltalake/pyarrow reader.
2. **phlo-sling has no Delta target connection.** Its auto-connection
   resolver builds only `PHLO_POSTGRES`, `PHLO_ICEBERG`, and `PHLO_S3`. The
   regions stream full-refreshes into a Parquet hand-off snapshot
   (`tgt_object` override) which the `delta_regions` asset merges into
   `raw.delta_regions` through the neutral table-store interface.
3. **phlo_delta's schema migrator crashes on deltalake >= 1.**
   `DeltaSchemaMigrator.diff_schema`/`apply_plan` call
   `DeltaTable.schema().to_pyarrow()`, which deltalake 1.x replaced with
   `to_arrow()` (resolved version here: 1.6.3). This example plans additive
   changes through phlo's provider-neutral planner with
   `DELTA_SCHEMA_POLICY` and applies them through delta-rs'
   non-destructive `alter.add_columns` instead.
4. **phlo_delta's table-stat helpers crash on deltalake >= 1** for the same
   class of reason (`DeltaTable.files()` removed).
   `scripts/delta_history.py` degrades gracefully: history prints,
   maintenance recommendations report themselves unavailable.
5. **No schema-mode merge on the append path.** `append_to_table` does not
   pass `schema_mode="merge"`, so a batch carrying the new column fails
   until the additive column exists. Planning plus applying the safe add is
   the documented operator procedure between firmware generations.

## Layout

```text
scripts/generate_fixtures.py    deterministic fixtures: hourly NDJSON.gz, corrections, evolved CSV, regions CSV, registry DB, labeled failures
scripts/replay_server.py        REST replay server for the evolved batch
scripts/seed_postgres.py        loads public.regions into the compose PostgreSQL source
scripts/delta_history.py        version history + maintenance inspection via the provider API
workflows/ingest/               DLT assets (readings, corrections, registry, regions) and schema-evolution helpers
workflows/sources/postgres/     Sling replication stream for the regions lookup
workflows/normalize/models/     stg_devices, stg_sites, stg_regions, telemetry_dedup (dedup + correction overlay)
workflows/aggregate/models/     device_health_hourly, fleet_daily_summary
workflows/publish/models/       device_health_current, site_daily_report (region-enriched)
workflows/quality/              operational validators (sequence, duplicates, partition integrity, corrections)
workflows/schemas/              Pandera contracts incl. the optional signal_quality_dbm field
workflows/schedules/            four stopped Dagster schedules, no WAP job
workflows/transforms/dbt/       one dbt project; model-paths span the three SQL stages
tests/                          fast deterministic contract/failure/Delta-engine tests
docker-compose.yml              PostgreSQL source for Sling (host port 10732, user/pass/db delta/delta/delta)
```

## Run the lakehouse

From this directory:

```bash
uv sync --locked --group dev
uv run python scripts/generate_fixtures.py
uv run pytest -q
uv run ruff check .
```

Start the platform and source database:

```bash
docker compose up -d
uv run python scripts/seed_postgres.py
uv run phlo services init --force --no-dev
uv run phlo services start --build
```

Serve the evolved batch replay (only needed when materializing the T06
firmware-v2 batch through REST):

```bash
uv run python scripts/replay_server.py
```

Materialize every asset in dependency order (references carry no partition;
raw readings require one):

```bash
uv run phlo materialize sling_delta_regions_snapshot
uv run phlo materialize dlt_device_registry
uv run phlo materialize dlt_site_directory
uv run phlo materialize dlt_delta_regions
uv run phlo materialize dlt_telemetry_readings --partition 2026-08-20
uv run phlo materialize dlt_telemetry_corrections --partition 2026-08-20
uv run phlo materialize stg_devices --partition 2026-08-20
uv run phlo materialize stg_sites --partition 2026-08-20
uv run phlo materialize stg_regions --partition 2026-08-20
uv run phlo materialize telemetry_dedup --partition 2026-08-20
uv run phlo materialize device_health_hourly --partition 2026-08-20
uv run phlo materialize fleet_daily_summary --partition 2026-08-20
uv run phlo materialize device_health_current --partition 2026-08-20
uv run phlo materialize site_daily_report --partition 2026-08-20
```

There is no `.phlo/wap-reports/` step to wait for: writes land on main
directly. Inspect history and maintenance state at any time:

```bash
uv run python scripts/delta_history.py raw.telemetry_readings raw.telemetry_corrections
```

## Expected results

The fixture fleet is 8 devices across 3 sites reporting six operating hours
on 2026-08-20 (T00-T05):

- Deliveries hold 293 rows: 288 distinct messages, 5 verbatim gateway
  retransmissions, and 4 stragglers measured in hours T01-T04 but delivered
  in the T05 file (`arrived_late`).
- `raw.telemetry_readings` lands 293 rows in one daily identity partition
  (`event_date=2026-08-20`) because the raw asset is append-only; distinct
  facts stay at 288.
- `raw.telemetry_corrections` merges 2 amendments: calibration offset
  `t-003-0104 -> temperature 24.5` and drift fix `t-006-0305 -> humidity 61`.
  Replaying the same batch reports `rows_updated: 2, rows_inserted: 0` and
  the table stays at exactly 2 rows - merge idempotency, proven by the test
  suite against the real delta-rs engine.
- A deliberately bad third correction merged afterwards is removed by
  restoring the last good version (`rollback_to_snapshot`), leaving the
  table back at 2 rows - Delta's substitute for WAP discard.
- The firmware-v2 batch (`generated-data/evolved/readings_v2.csv`, hour T06,
  48 rows) carries `signal_quality_dbm`. After planning the single `add`
  change (classified `safe`) and applying it, the evolved batch appends onto
  the same table: 341 rows total, v1 rows reading NULL for the new column.
- `telemetry_dedup` collapses to exactly 288 rows with corrections overlaid
  and 4 rows flagged `arrived_late`; `device_health_hourly` holds 48 rows
  (8 devices x 6 hours) before the evolved batch, accounting for all 288
  readings.
- `fleet_daily_summary` reports completeness 1.0 for all three sites with
  reading counts 108 / 108 / 72 (north / south / east);
  `site_daily_report` joins site names and the Sling-replicated regions
  (North Cluster / NL, South Cluster / PT, East Cluster / PL) onto those
  rows.

Re-ingesting the same day doubles the raw table (append semantics) while
distinct facts stay stable:

| Table | After first run | After re-ingesting the same day |
| --- | --- | --- |
| `telemetry_readings` | 293 rows / 288 distinct | 586 rows / 288 distinct |
| `telemetry_dedup` | 288 | 288 |

## Expected failures

Each labeled fixture under `generated-data/failures/` breaks exactly one
invariant, proven by `tests/test_delta_portability.py`:

- `readings_out_of_bounds.ndjson.gz`: temperature 999 fails the blocking
  physical-bound contract. Validation runs before any write, so main stays
  untouched - but unlike the Iceberg sibling there is no branch holding the
  partial result; the failed run simply never reaches a write.
- `readings_sequence_regression.ndjson.gz`: a device sequence number moves
  backwards and fails `assert_sequence_monotonic`.
- `readings_unknown_device.ndjson.gz`: `dev-999` is absent from the registry
  and fails `assert_registered_devices_only`.
- `readings_duplicate_burst.ndjson.gz`: a ~50% duplicate batch exceeds the
  2% threshold and fails `assert_duplicate_ratio_within_threshold`.
- `evolved_signal_out_of_bounds.csv`: `signal_quality_dbm = -9` violates the
  additive column's bound (-120..-40) and fails the shared contract.

To reproduce the fail-closed path live, copy the out-of-bounds file into a
new `generated-data/telemetry/hour=<label>/readings.ndjson.gz`, re-materialize
the readings asset, and observe the failure with unchanged row counts.

## Schedules

Four schedules register with Dagster, all `STOPPED` so an example checkout
never launches work unexpectedly. There is deliberately no WAP job.

| Schedule | Cron | Job |
|---|---|---|
| hourly ingestion | `20 * * * *` | append the hour's deliveries |
| rolling repair | `40 * * * *` | merge corrections, rebuild dedup + hourly health + current |
| daily reference build | `15 1 * * *` | regions snapshot + merge, registry references, fleet summary, site report |
| weekly reconciliation | `0 3 * * 1` | straight full pass over every asset on main |

Asset settings mirror the Iceberg sibling one-for-one (same freshness
windows, retry budgets, owners, consumers, SLAs) because the portability
comparison varies exactly one thing: the table store.

## Profile maturity

Non-default data plane: Delta Lake via `phlo-delta` (delta-rs), MinIO object
storage, Trino query engine for Iceberg only. The example is CI-first:
pytest needs no containers (the Delta-backed tests run the real delta-rs
engine against a throwaway local warehouse), and the documented live path is
deterministic because every input byte is generated, not recorded.

## Platform requirements and known semantics

Requires the standard phlo runtime plus `phlo-delta`; `phlo[defaults]` keeps
Iceberg installed alongside Delta, and the explicit routing decides who owns
each write. DLT normalizes ISO-8601 strings to timestamps during staging, so
temporal contract fields are typed natively; `event_date` is the daily
identity partition (Delta cannot transform timestamps into hours the way the
Iceberg sibling does). The SQLite registry is read read-only and relocatable
via `DELTA_REGISTRY_DB`; point `REGIONS_SOURCE_URL` at any PostgreSQL
carrying `public.regions`. Live Delta writes need MinIO running and
`DELTA_WAREHOUSE_PATH` reachable; tests and scripts work against a plain
local directory by setting the same variable.
