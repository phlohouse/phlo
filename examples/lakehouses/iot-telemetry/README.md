# IoT Telemetry lakehouse

A pipeline-stage Phlo lakehouse that ingests hourly compressed telemetry
deliveries, repairs late data, and publishes fleet health products. It exists
to answer one question: when deliveries arrive late or get retransmitted, can
reprocessing repair aggregates without duplicating events - and where are the
practical volume limits?

The project owns its uv environment, deterministic fixtures, workflow
configuration, and a local device registry database. It does not depend on
another example's runtime state.

## What it exercises

| Area | Coverage |
|---|---|
| Ingestion | `phlo.ingest.dlt` assets appending gzip-compressed NDJSON hourly files, merging late correction batches by `message_id`, and merging two reference tables out of a SQLite registry database |
| Transforms | Pipeline-stage dbt models through one project with stage-scoped model paths: deduplication plus correction overlay (`normalize`), hourly health and daily fleet summaries (`aggregate`), consumer-facing current health and site report (`publish`) |
| Quality | Physical bounds via blocking Pandera contracts at ingest; sequence monotonicity, duplicate ratio, known-device, and file-count-pressure validators over plain DataFrames; labeled failure fixtures per invariant |
| Orchestration | Hourly ingestion, rolling late-data repair, daily fleet build, weekly full WAP reconciliation; all schedules stopped by default |
| Partitions | Raw telemetry is identity-partitioned by `event_hour`; runs are daily with bounded parallel backfills |
| Data plane | Iceberg tables in MinIO via Nessie catalog, Trino query engine, WAP branch promotion |

## Layout

```text
scripts/generate_fixtures.py    deterministic fixtures: hourly NDJSON.gz, corrections, registry DB, labeled failures
workflows/ingest/               DLT ingestion assets (readings, corrections, registry)
workflows/normalize/models/     stg_devices, stg_sites, telemetry_dedup (dedup + correction overlay)
workflows/aggregate/models/     device_health_hourly, fleet_daily_summary
workflows/publish/models/       device_health_current, site_daily_report
workflows/quality/              operational validators (sequence, duplicates, devices, file pressure)
workflows/schemas/              Pandera contracts
workflows/schedules/            four stopped Dagster schedules
workflows/transforms/dbt/       one dbt project; model-paths span the three SQL stages
tests/                          fast deterministic contract/failure tests
```

The dbt project compiles models that physically live inside the stage folders:

```yaml
model-paths:
  - "../../normalize/models"
  - "../../aggregate/models"
  - "../../publish/models"
```

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

Materialize every asset in dependency order, waiting for each WAP report in
`.phlo/wap-reports/` to reach `promoted` before launching dependents:

```bash
uv run phlo materialize dlt_device_registry
uv run phlo materialize dlt_site_directory
uv run phlo materialize dlt_telemetry_readings --partition 2026-08-20
uv run phlo materialize dlt_telemetry_corrections --partition 2026-08-20
uv run phlo materialize stg_devices --partition 2026-08-20
uv run phlo materialize stg_sites --partition 2026-08-20
uv run phlo materialize telemetry_dedup --partition 2026-08-20
uv run phlo materialize device_health_hourly --partition 2026-08-20
uv run phlo materialize fleet_daily_summary --partition 2026-08-20
uv run phlo materialize device_health_current --partition 2026-08-20
uv run phlo materialize site_daily_report --partition 2026-08-20
```

Every ingestion asset requires an explicit partition key (`YYYY-MM-DD`);
the day's hourly files are appended inside one WAP-isolated run.

Inspect results:

```bash
uv run phlo trino --execute 'SELECT count(*), count(DISTINCT message_id) FROM iceberg.raw.telemetry_readings'
uv run phlo catalog tables
```

Bounded parallel backfills reuse the same WAP path per partition:

```bash
uv run phlo backfill dlt_telemetry_readings --partitions 2026-08-19,2026-08-20 --parallel 2
```

## Expected results (verified end to end)

The fixture fleet is 8 devices across 3 sites reporting six operating hours on
2026-08-20 (hours T00-T05):

- Deliveries hold 533 rows, of which 5 are verbatim gateway retransmissions
  (528 distinct messages) and 4 are stragglers measured in hours T01-T04 but
  delivered in the T05 file (`arrived_late`).
- `telemetry_readings` lands 533 rows in six hourly Iceberg partitions
  (89/89/89/89/89/88) because the raw asset is append-only.
- `telemetry_corrections` merges 2 amendments: calibration offset
  `m-003-0004 -> temperature 24.5` and drift fix `m-006-0307 -> humidity 61`.
- `telemetry_dedup` collapses to exactly 528 rows with the corrections
  overlaid and 4 rows flagged `arrived_late`.
- `device_health_hourly` holds 48 rows (8 devices x 6 hours) accounting for
  all 528 readings; `device_health_current` keeps the newest hour per device.
- `fleet_daily_summary` reports completeness 1.0 for all three sites with
  reading counts 198 / 198 / 132 and late arrivals 2 / 1 / 1;
  `site_daily_report` joins site names and regions onto those rows.

Replay proves the headline property. Re-materializing the same day doubles the
raw table (append semantics) while distinct facts stay stable:

| Table | After first run | After re-ingesting the same day |
| --- | --- | --- |
| `telemetry_readings` | 533 rows / 528 distinct | 1,066 rows / 528 distinct |
| `telemetry_dedup` | 528 | 528 |
| readings counted by `device_health_hourly` | 528 | 528 |

Rebuilding the normalize and aggregate stages after a correction batch is the
rolling repair: aggregates change only where corrected values apply, and no
duplicate event ever reaches them.

## Expected failures

Each labeled fixture under `generated-data/failures/` breaks exactly one
invariant, proven by `tests/test_iot_telemetry.py`:

- `readings_out_of_bounds.ndjson.gz`: temperature 999 fails the blocking
  physical-bound contract. Materializing it leaves the WAP report in terminal
  `failed` while the published catalog stays untouched (verified: main held
  1,066 rows after the failed run).
- `readings_sequence_regression.ndjson.gz`: a device sequence number moves
  backwards and fails `assert_sequence_monotonic`.
- `readings_unknown_device.ndjson.gz`: `dev-999` is absent from the registry
  and fails `assert_registered_devices_only`.
- `readings_duplicate_burst.ndjson.gz`: a 60% duplicate batch exceeds the 2%
  threshold and fails `assert_duplicate_ratio_within_threshold`.
- `failures/pressure/hour=...`: 40 delivery files in one hour exceed the
  24-files-per-hour maintenance threshold and fail
  `assert_file_count_within_threshold`.

To reproduce the fail-closed path live, copy the out-of-bounds file into a new
`generated-data/telemetry/hour=<label>/readings.ndjson.gz`, re-materialize the
readings asset, and observe the failed WAP report plus an unchanged catalog.

## Schedules

Four schedules register with Dagster, all `STOPPED` so an example checkout
never launches work unexpectedly:

| Schedule | Cron | Job |
|---|---|---|
| hourly ingestion | `20 * * * *` | append the hour's deliveries |
| rolling repair | `40 * * * *` | merge corrections, rebuild dedup + hourly health + current |
| daily fleet build | `15 1 * * *` | refresh registry references, fleet summary, site report |
| weekly reconciliation | `0 3 * * 1` | full WAP pass over every asset |

Asset settings are justified by source behavior: short freshness windows and a
five-retry budget reflect bursty radio deliveries; reference tables get long
windows and single retries; corrections merge because replays must be
idempotent; raw telemetry appends because the gateway already retransmits.

## Profile maturity

Blessed Iceberg stack (MinIO + Nessie + Trino). The example is CI-first:
pytest needs no containers, and the documented live path is deterministic
because every input byte is generated, not recorded.

## Platform requirements and known semantics

Requires phlo-dagster runtime images built after #766 (glibc base), matching
the other examples. DLT normalizes ISO-8601 strings to timestamps during
staging, so temporal contract fields are typed natively and `event_hour`
becomes a timestamptz hourly identity partition. The registry database is
SQLite read read-only by the ingest assets; point `IOT_REGISTRY_DB` elsewhere
to swap registries without touching workflow code.
