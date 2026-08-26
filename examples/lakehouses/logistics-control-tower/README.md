# Logistics Control Tower lakehouse

A hybrid Phlo lakehouse that converges three heterogeneous sources - Sling
replication from a PostgreSQL order database, DLT polling of two carrier event
APIs, and DLT ingestion of warehouse scan CSVs - into one canonical shipment
state with SLA marts. It exists to answer one question: when carriers report
contradictory event histories for the same shipment, does the platform decide
shipment state from data instead of arrival order, and do contradictions stay
visible instead of silently resolving?

The project owns its uv environment, deterministic fixtures, replay API, and
local source database. It does not depend on another example's runtime state.

## What it exercises

| Area | Coverage |
|---|---|
| Sources | Local PostgreSQL (`docker-compose.yml`, port 10332) seeded by `scripts/seed_postgres.py --stage base\|update`; a tiny replay HTTP server (`scripts/carrier_api.py`) serving per-carrier per-day event scans; one CSV file per warehouse |
| Ingestion | One `phlo.ingest.sling` incremental stream (`updated_at` watermark); five `phlo.ingest.dlt` assets: two carrier feeds merging on `event_id` with different polling cadences, warehouse scans merging on `scan_id`, and two `partitioned=False` reference merges (carrier directory, SLA terms) |
| Transforms | Repeated Python transform folders (`orders/transforms`, `carriers/transforms`, `warehouses/transforms`, `control_tower/transforms`) registering real Dagster assets discovered straight from workflow modules; results land in Iceberg through the `phlo_iceberg` helpers (`ensure_table` + `pandera_to_iceberg` + `merge_to_table`) |
| Convergence | Explicit cross-folder dependencies: the control-tower grid depends on orders, carriers, and warehouses outputs at once |
| Quality | Blocking `quality_checks` gate unknown-carrier references at carrier ingestion; the orders status-regression gate runs in the first Python transform (Sling assets expose no quality hook - see Platform notes); canonical-state ordering rejects ambiguous timestamp ties; labeled failure fixtures each break exactly one invariant |
| Orchestration | 20-minute order increments, hourly ATLAS vs four-hourly CORSAIR carrier polls, nightly reference refresh, daily marts, weekly WAP reconciliation; all schedules stopped |
| Data plane | Iceberg tables in MinIO via Nessie catalog, Trino query engine, WAP branch promotion |

## Layout

```text
scripts/generate_fixtures.py        deterministic fixtures: orders, carrier scans, warehouse CSVs, references, labeled failures
scripts/carrier_api.py              replay HTTP server serving carrier scan fixtures
scripts/seed_postgres.py            seeds the source PostgreSQL (--stage base|update)
workflows/schemas/                  Pandera contracts (Series[datetime] temporal fields)
workflows/orders/                   Sling replication of public.orders
workflows/orders/transforms/        version collapse + status-regression gate -> order_current_state
workflows/carriers/                 DLT carrier feeds + reference tables
workflows/carriers/transforms/      feed unification, exception queue, coverage
workflows/warehouses/               DLT warehouse scan CSVs
workflows/warehouses/transforms/    dwell pairing, unclosed-scan anomalies
workflows/control_tower/transforms/ cross-domain shipment grid
workflows/control_tower/transforms/dbt/   central dbt project: canonical_shipment_state, transit_duration, sla_mart
workflows/schedules/                six stopped Dagster schedules
tests/                              fast deterministic contract/failure tests
```

### Name collision, resolved deliberately

The carriers and warehouses domains both naturally produce a per-shipment
exception view, and both initially registered an asset named
`shipment_exceptions`. Duplicate asset keys collide because the framework
merges definitions from every workflow module. The name stays in the carriers
domain - carrier-reported exceptions are the operational source of truth the
control tower reacts to - while the warehouses view was renamed to
`warehouse_scan_exceptions`. The resolution is documented in both modules'
docstrings and asserted by a test.

## Run the lakehouse

From this directory:

```bash
uv sync --locked --group dev
uv run python scripts/generate_fixtures.py --scenario update
uv run pytest -q
uv run ruff check .
```

Start the source database and replay server:

```bash
docker compose up -d logistics-postgres
uv run python scripts/seed_postgres.py --stage base
uv run python scripts/carrier_api.py &          # port 8090, serves generated-data/carriers
```

Start the platform:

```bash
uv run phlo services init --force --no-dev
uv run phlo services start --build
uv run phlo doctor
```

Materialize every asset in dependency order:

```bash
uv run phlo materialize dlt_carrier_directory
uv run phlo materialize dlt_sla_terms
uv run phlo materialize sling_shipments_orders
uv run phlo materialize dlt_carrier_events_atlas --partition 2026-08-10
uv run phlo materialize dlt_carrier_events_corsair --partition 2026-08-10
uv run phlo materialize dlt_warehouse_scans --partition 2026-08-10
uv run phlo materialize order_current_state
uv run phlo materialize carrier_events_unified
uv run phlo materialize shipment_exceptions
uv run phlo materialize carrier_coverage
uv run phlo materialize warehouse_dwell
uv run phlo materialize warehouse_scan_exceptions
uv run phlo materialize control_tower_shipment_grid
uv run phlo dbt compile
```

Then prove incremental replication picks up exactly the delta:

```bash
uv run python scripts/seed_postgres.py --stage update
uv run phlo materialize sling_shipments_orders   # only rows newer than updated_at watermark
```

## Expected results

The fixture window covers 2026-08-10..12: 24 distinct orders (ORD-1001..1024,
27 replicated versions including in-place advancements), 18 shipments
(SHP-2001..2018), 54 carrier events split evenly ATLAS 27 / CORSAIR 27 across
9 shipments each, 36 warehouse scans (inbound/outbound pairs per shipment).

- Per-day carrier scans: 16 events on 08-10, 10 on 08-11, 1 on 08-12 for each
  carrier; replays merge idempotently on `event_id`.
- After `--stage update`: the incremental Sling run appends exactly 10 delta
  rows (6 new orders ORD-1025..1030 plus 4 in-place status advancements);
  updated orders arrive as additional versions collapsed read-time.
- `order_current_state` holds exactly 24 rows.
- `carrier_events_unified` deduplicates both feeds back to 54 events.
- `shipment_exceptions` holds exactly 1 row: SHP-2018, whose feed reports a
  delivery at 14:00 followed by an exception at 18:00 UTC on 2026-08-11.
- `carrier_coverage`: ATLAS 27 events / 9 shipments, CORSAIR 27 / 9.
- `warehouse_dwell`: 18 rows with dwell hours cycling 4/6/8/10;
  `warehouse_scan_exceptions` is empty (every fixture shipment scans out).
- The dbt mart `canonical_shipment_state` resolves SHP-2018 to `exception`
  (later event time wins over the earlier delivered event) with
  `contradiction_count = 1`; SHP-2017 (exception cleared by a later delivery)
  resolves to `delivered` with `contradiction_count = 1`; the other 16
  shipments are clean.
- `transit_duration` holds 17 delivered shipments (16 clean plus the SHP-2017
  recovery). SHP-2018 stays out of the SLA mart entirely while its
  contradiction remains flagged upstream.
- `sla_mart` flags exactly 4 breaches against standard service levels:
  SHP-2004 (32h vs 26h), SHP-2008 (29h vs 26h), SHP-2014 (32h vs 26h, 6h over),
  SHP-2009 (32h vs 30h).

## Expected failures

Each labeled fixture under `generated-data/failures/` breaks exactly one
named invariant, proven by `tests/test_logistics_control_tower.py`:

- `orders_status_regression.csv`: a later order version moves the status from
  `delivered` back to `shipped`; fails the orders status-regression gate.
- `events_unknown_carrier.json`: an event referencing unregistered carrier
  `ZEPHYR`; fails the blocking carrier-ingestion `quality_checks` gate.
- `events_ambiguous_state.json`: `delivered` and `exception` events sharing
  the maximal `event_time` leave the winner dependent on row order;
  `assert_unambiguous_event_order` raises instead of guessing.
- `sla_terms_negative.csv`: an SLA term of `-6` hours can never be met;
  fails `assert_sla_clock_positive`.

The contradiction fixture itself is the headline case: delivered-after-
exception normally reads as recovery (SHP-2017), so exception-after-delivered
must flip state to `exception` AND raise `contradiction_count` rather than
pick whichever event arrived last.

## Schedules

Six schedules register with Dagster, all `STOPPED` so an example checkout
never launches work unexpectedly:

| Schedule | Cron | Job |
|---|---|---|
| order increments | `*/20 * * * *` | Sling `shipments_orders` replication |
| ATLAS polling | `10 * * * *` | hourly carrier feed |
| CORSAIR polling | `35 */4 * * *` | four-hourly carrier feed |
| reference refresh | `15 2 * * *` | carrier directory + SLA terms |
| daily marts | `40 2 * * *` | all transforms through the SLA mart |
| weekly reconciliation | `0 3 * * 1` | full WAP pass over every asset |

Asset settings are justified by source behavior: carrier feeds get short
freshness windows and retries matching their cadences; reference tables get
week-long windows, single retries, and opt out of partitioning entirely;
warehouse scans sit between the two because sites batch their files daily.

## Profile maturity

Blessed Iceberg stack (MinIO + Nessie + Trino) with Sling as an optional
ingestion capability. CI-first: pytest needs no containers, and the live path
is deterministic because every input byte is generated, never recorded - even
the "carrier API" is a byte-exact replay server.

## Platform requirements and known semantics

- Requires phlo-dagster runtime images built after #766 (glibc base), matching
  the other examples.
- `phlo.ingest.sling` exposes no `quality_checks` hook, so the orders status
  regression gate lives in the first orders Python transform instead of at
  ingestion; sling assets also cannot carry pandera validation schemas. Both
  would need platform support to move the gate before staging.
- Dagster asset owners must be emails or `team:`-prefixed names; domain-style
  owner strings are kept as phlo metadata only.
- Dagster (1.13.x) rejects `context: dg.AssetExecutionContext` annotations on
  asset functions decorated above `from __future__ import annotations`
  modules, so context parameters are left unannotated in transform modules.
- `phlo_iceberg.merge_to_table` upserts by deleting matching keys then
  appending; duplicate keys within one batch are warned, not merged.
- The replay server binds host-side, so the compose services reach it via the
  `host.docker.internal` extra hosts declared in `phlo.yaml`.
