# ClickHouse operational lakehouse

A domain-organized Phlo lakehouse that runs the whole data plane on
ClickHouse: DLT micro-batches land platform events and access logs, Sling
replicates tenant metadata out of a local PostgreSQL source, and a
dbt-clickhouse project serves error-rate, latency, throughput, and tenant
usage marts. It exists to answer one question: what actually changes when one
provider fills the store, query, and publish roles instead of the bundled
Iceberg/Trino/Postgres stack - and which assumptions silently break?

The project owns its uv environment, deterministic fixtures, workflow
configuration, and a local metadata database. It does not depend on another
example's runtime state.

## What it exercises

| Area | Coverage |
|---|---|
| Ingestion | `phlo.ingest.dlt` assets appending quarter-hour platform-event micro-batches and hourly access-log files; `phlo.ingest.sling` snapshotting the tenant directory from PostgreSQL (`chmeta-postgres`, host port 10832) |
| Transforms | One dbt-clickhouse project: read-time replay deduplication (staging views), appended hourly marts (`error_rate_hourly`, `latency_p95_hourly`, `throughput_hourly`), and the replacing daily aggregate (`tenant_usage_daily` under `ReplacingMergeTree`) |
| Quality | Latency bounds and status-code catalog via blocking Pandera contracts at ingest; event-type and path gates through `quality_checks`; tier-1 per-hour freshness and hourly-vs-daily count reconciliation over plain DataFrames; labeled failure fixtures per invariant |
| Orchestration | `*/15` minute micro-batch ingestion, hourly mart refresh at :10, nightly tenant metadata at 02:30; all schedules stopped by default |
| Partitions | Platform events are identity-partitioned by `occurred_hour`; access logs hour-partitioned on `occurred_at`; runs are daily with bounded runtime (300 s) |
| Data plane | ClickHouse as `table_store`, `query_engine`, and `publish_target`; no Iceberg, Nessie, Trino, or WAP anywhere |

## Layout

```text
docker-compose.yml              chmeta-postgres tenant metadata source (10832 -> 5432)
scripts/generate_fixtures.py    deterministic fixtures: micro-batches, logs, tenants CSV, labeled failures
scripts/seed_postgres.py        load generated-data/accounts/tenants.csv into the metadata source
workflows/platform_events/      DLT ingestion of quarter-hour event micro-batches (with verbatim replays)
workflows/access_logs/          DLT ingestion of hourly request logs
workflows/accounts/             Sling tenant snapshot targeting ClickHouse via PHLO_CLICKHOUSE_CONN
workflows/operational_marts/dbt/  one dbt-clickhouse project (profiles, staging dedup views, serving marts)
workflows/quality/              validators: bounds, status catalog, tier-1 freshness, reconciliation, p95 helper
workflows/schemas/contracts.py  Pandera contracts (latency 0..60000 ms, status catalog, tiers)
workflows/schedules/ops.py      three stopped Dagster schedules
tests/                          fast deterministic contract/failure/routing tests
```

## Run the example

From this directory:

```bash
uv sync --locked --group dev
uv run python scripts/generate_fixtures.py
uv run pytest -q
uv run ruff check .
```

Start the platform (ClickHouse replaces the Iceberg/Trino services):

```bash
docker compose up -d chmeta-postgres
uv run python scripts/seed_postgres.py
uv run phlo services init --force --no-dev
uv run phlo services start --build
uv run phlo doctor
```

Materialize every asset in dependency order. Micro-batch assets require an
explicit day partition; reference-sized streams materialize without one:

```bash
uv run phlo materialize dlt_platform_events --partition 2026-08-20
uv run phlo materialize dlt_access_logs --partition 2026-08-20
uv run phlo materialize sling_chmeta_tenants
uv run phlo materialize stg_platform_events_dedup
uv run phlo materialize stg_access_logs_dedup
uv run phlo materialize error_rate_hourly
uv run phlo materialize latency_p95_hourly
uv run phlo materialize throughput_hourly
uv run phlo materialize tenant_usage_daily
```

Inspect results directly against the ClickHouse query engine - no Trino hop:

```bash
uv run phlo clickhouse query "SELECT count(*), uniqExact(event_id) FROM raw.platform_events"
uv run phlo clickhouse query "SELECT * FROM marts.tenant_usage_daily FINAL"
```

## Expected results (fixture arithmetic)

The fixture window is four operating hours on 2026-08-20 (T00-T03), three
tenants (two tier-1: `t-northwind`, `t-acme`; one tier-2: `t-globex`):

- `platform_events` lands 60 raw rows across 16 quarter-hour batch files:
  48 distinct events (4 hours x 3 tenants x 4 slots) plus 12 verbatim
  replays (3 per hour). Latencies span 100-368 ms inside the 0..60000 ms
  bound; event types split evenly 12/12/12/12.
- `access_logs` lands 84 requests, exactly 21 per hour, all durations
  distinct within an hour; statuses mix 200x49, 204x14, 400x11, and 10
  server errors (500x3, 502x4, 503x3).
- Read-time deduplication collapses raw to 48 events / 84 requests no matter
  how often deliveries replay.
- `throughput_hourly`: 21 requests per hour (7 per tenant per hour).
- `error_rate_hourly`: 14.29% in T00/T01 (3 errors each), 9.52% in T02/T03
  (2 errors each); daily per-tenant errors 3 / 4 / 3.
- `latency_p95_hourly` via `quantileExact(0.95)` is exact because rank
  ceil(0.95*21)=20 coincides with the interpolated position 0.95*20=19:
  **5530, 5710, 5585, 5460 ms** for T00-T03.
- `tenant_usage_daily` holds one replacing row per tenant:
  `t-northwind` 16/28/3, `t-acme` 16/28/4, `t-globex` 16/28/3
  (events/requests/errors). Hourly sums equal these totals exactly.

Replay proves the headline property. Re-materializing the same day doubles
the raw tables while every mart stays stable:

| Table | After first run | After re-ingesting the same day |
| --- | --- | --- |
| `raw.platform_events` | 60 rows / 48 distinct | 120 rows / 48 distinct |
| `stg_platform_events_dedup` | 48 | 48 |
| `raw.access_logs` | 84 rows / 84 distinct | 168 rows / 84 distinct |
| counts summed by `tenant_usage_daily` | 28 requests per tenant | 28 requests per tenant |

## Expected failures

Each labeled fixture under `generated-data/failures/` breaks exactly one
invariant, proven by `tests/test_clickhouse_ops.py`:

- `platform_events_latency_out_of_bounds.ndjson.gz`: latency_ms 60001 fails
  the blocking bounds contract and nothing else.
- `access_logs_status_code_unknown.ndjson.gz`: status_code 599 falls outside
  the documented response catalog and nothing else.
- `platform_events_tier1_gap.ndjson.gz`: an hour carrying only `t-globex`
  traffic trips the tier-1 freshness validator while staying bounds-clean.
- `reconciliation_shortfall.csv`: honest daily totals with `t-globex`'s final
  hour removed (13/21/2 instead of 16/28/2) breaks the hourly-vs-daily count
  reconciliation while the other tenants still match.

There is no WAP-failure fixture: this data plane has no WAP to fail.

## Capability routing and reality check

`phlo.yaml` routes all three roles to the single ClickHouse provider
(`phlo-clickhouse` registers `table_store:clickhouse`,
`query_engine:clickhouse`, `publish_target:clickhouse`):

```yaml
capabilities:
  defaults:
    table_store: clickhouse
    query_engine: clickhouse
    publish_target: clickhouse
wap:
  enabled: false
```

What ClickHouse genuinely covers here:

- Store: MergeTree tables created by `ClickHouseResource.ensure_table`
  (partition columns become `PARTITION BY`), rows appended from staged
  Parquet. Schema evolution is supported; snapshots are not.
- Query: dbt-clickhouse runs the marts directly against ClickHouse SQL
  (`countIf`, `quantileExact`, `toStartOfHour`). The modest sub-second
  query-latency target for the hourly panels needs no external engine.
- Publish: the same service serves the marts to consumers as publish target.

What stays coupled to the Iceberg/Trino world, and why:

- No WAP: ClickHouse registers `supports_snapshots=false` /
  `supports_time_travel=false`, so branch-isolated write-audit-publish does
  not exist and `wap.enabled` is false. Audit-before-publish would need
  staging tables and manual swaps; the replacing aggregate uses
  `ReplacingMergeTree` + `FINAL` instead.
- No merge-on-read history: raw appends accumulate verbatim replays with no
  snapshot isolation to hide them; correctness depends entirely on read-time
  deduplication (`row_number()` collapse) in the staging views.
- Mutations are asynchronous: `merge_parquet` deletes via background
  mutations before re-inserting, so readers can transiently see both
  versions of a key - another reason dedup lives in queries, not storage.
- File/branch semantics from Trino examples (`partitioned=False` reference
  merges, branch CTAS) have no equivalent here; the accounts stream targets
  ClickHouse directly instead of a catalog.

## Schedules

Three schedules register with Dagster, all `STOPPED` so an example checkout
never launches work unexpectedly:

| Schedule | Cron | Job |
|---|---|---|
| micro-batch ingestion | `*/15 * * * *` | append new platform-event batches and access-log hours |
| hourly mart refresh | `10 * * * *` | append newest hour into the three hourly marts, rebuild `tenant_usage_daily` |
| nightly metadata | `30 2 * * *` | Sling snapshot of the tenant directory |

Asset settings follow source behavior: short freshness windows (1-3 h) and a
300 s runtime bound reflect frequent micro-batches; the tenant snapshot gets
a long window because the directory barely changes.

## Profile maturity

Preview ClickHouse data plane (phlo-clickhouse). The example is CI-first:
pytest needs no containers, and every input byte is generated, not recorded.
The live path additionally requires Docker for the `chmeta-postgres` source
and a running ClickHouse service.

## Platform requirements and known gaps

- Requires `phlo-clickhouse>=0.14` installed; it is listed as a direct
  dependency because `phlo[defaults]` bundles the Iceberg/Trino stack, not
  ClickHouse.
- phlo-sling auto-discovers `PHLO_POSTGRES` / `PHLO_ICEBERG` / `PHLO_S3`
  connections but has no ClickHouse auto-connection; this example injects an
  explicit `PHLO_CLICKHOUSE_CONN` JSON env var (phlo.yaml) and returns it as
  `tgt_conn`. A first-class ClickHouse auto-connection would remove this
  seam.
- dbt support ships behind the `phlo-clickhouse[dbt]` extra; this example
  pins `dbt-clickhouse>=1.10,<1.11` in its dev group and uses profile shape
  `type: clickhouse` with `host/port/user/password/schema` (database must be
  omitted or equal schema).
- phlo-dbt regenerates `profiles.yml` from `DbtSettings` whenever its hooks
  or plugin discovery run. The example routes that generator to ClickHouse
  through the `DBT_QUERY_*` entries in phlo.yaml env (mirrored in
  `tests/conftest.py` for the test session). Two sharp edges found while
  verifying: outside an initialized project (no `.phlo/.env`) a bare
  capability-discovery run regenerates default trino settings and clobbers
  the checked-in ClickHouse profile; and the rendered payload always carries
  trino-specific keys (`method`, `catalog`, `http_scheme`). The adapter
  tolerates those extras at parse time (verified with `dbt parse` against
  dbt-clickhouse 1.10), but emitting engine-appropriate keys would remove
  the fragility.
- The dev group keeps `duckdb` for parity with sibling examples' local
  tooling; nothing in this example imports it. `dbt-trino` is intentionally
  absent - there is no Trino role to configure.
- Not verified without containers (documented path only): actual ClickHouse
  materialization, Sling replication against `chmeta-postgres`, and
  `ReplacingMergeTree FINAL` read behavior. Fixture arithmetic, contract
  validation, routing, and SQL evidence are covered deterministically by
  pytest.
