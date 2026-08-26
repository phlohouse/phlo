# Public Data Research lakehouse

A source-oriented Phlo lakehouse that lands three public-shaped feeds - a
paginated civic place API, monthly ZIP/CSV weather bulk archives, and annual
demographic files - into Iceberg and rebuilds subject-oriented research
models on top. It exists to answer one question: can researchers reproduce
an old result from table history after an upstream source revises its data,
and do mixed-grain rollups still reconcile when sources drift?

The project owns its uv environment, deterministic fixtures, replay server,
and workflow configuration. It does not depend on another example's runtime
state.

## What it exercises

| Area | Coverage |
|---|---|
| Sources | One replay API serving a paginated place registry (`/v1/places`); ZIP archives holding per-day CSV members; a GeoJSON metadata document; annual CSV files. Live/replay modes switch via `CIVIC_API_URL` |
| Ingestion | Four `phlo.ingest.dlt` assets: registry merges pages by `place_id`, GeoJSON flattens to a reference merge (`partitioned=False`), observations merge on an `observation_key` surrogate of `(station_id, observed_at)`, demographics merges the `(region, year)` surrogate |
| Partitions | Mixed temporal grains via identity partition specs: civic daily (`registry_date`), weather monthly (`obs_month`), demographics annual (`census_year`) - all fed by daily partition keys |
| Schema drift | July 2026 archives add `pressure_hpa`; the contract declares it optional so pre-drift months and the drift batch validate under one schema |
| Transforms | Geographic normalization (upper/trim names, region normalization), exact Fahrenheit-to-Celsius staging conversion, station-month indicators, annual rollup joined to demographics |
| Quality | Station coverage gate as a blocking `quality_checks` entry (orphan stations fail closed), strict numeric contracts at ingest, cross-grain precipitation reconciliation in `annual_rollup` |
| Scheduling | Daily civic ingestion, monthly weather, annual demographics, daily research rebuild, weekly full WAP reconciliation; all schedules stopped |

## Layout

```text
scripts/generate_fixtures.py   deterministic fixtures: paginated registry, GeoJSON, zips, demographics, labeled failures
scripts/civic_api.py           replay API serving the paginated place registry
workflows/sources/civic_api/   places_registry (daily) and places_geo (reference merge) ingestion
workflows/sources/weather_files/       archive reader, observations ingestion (monthly), coverage gate
workflows/sources/demographics/        annual file ingestion keyed by (region, year)
workflows/schemas/             Pandera contracts (including the optional drift column)
workflows/research/schedules.py        five stopped Dagster schedules
workflows/research/places/models/      subject-oriented dbt models (places)
workflows/research/indicators/         stg_observations, monthly_indicators, annual_rollup + pandas mirrors
workflows/transforms/dbt/      one dbt project; model-paths span places/ and indicators/
tests/                         fast deterministic contract/failure tests
```

The dbt project compiles models that physically live inside the research
folders:

```yaml
model-paths:
  - "../../places/models"
  - "../../indicators/models"
```

## Run the lakehouse

From this directory:

```bash
uv sync --locked --group dev
uv run python scripts/generate_fixtures.py
uv run python scripts/civic_api.py --port 8094 &   # deterministic replay API
uv run pytest -q
uv run ruff check .
```

Start the platform, then materialize in dependency order:

```bash
uv run phlo services init --force --no-dev
uv run phlo services start --build

uv run phlo materialize dlt_places_geo
uv run phlo materialize dlt_places_registry --partition 2026-08-10
uv run phlo materialize dlt_places_registry --partition 2026-08-11   # upstream revision page

uv run phlo backfill dlt_weather_observations --partitions 2026-05-01,2026-06-01,2026-07-01 --parallel 3
uv run phlo materialize dlt_region_demographics --partition 2026-01-01
uv run phlo materialize dlt_region_demographics --partition 2025-01-01

uv run phlo materialize stg_observations --partition 2026-06-01
uv run phlo materialize places --partition 2026-06-01
uv run phlo materialize monthly_indicators --partition 2026-06-01
uv run phlo materialize annual_rollup --partition 2026-06-01
```

Every raw asset merges on stable keys, so replays are idempotent; a daily
weather partition re-reads its whole calendar-month archive.

## Expected results (verified end to end)

The fixture window is three monthly archives (May-July 2026), four stations,
five registry places across two regions, and two demographic years.

- The registry serves five baseline places across two pages (page size 3);
  the later page restates exactly one record, `P3`, whose population moves
  `120000` to `121500`. After both dates merge, `dlt_places_registry` holds
  five rows and the revision changed exactly that one field.
- GeoJSON metadata parses to five reference rows; centroids derive from the
  polygon rings (each sits at `lat + 0.01, lon - 0.02`), and properties
  flatten to `prop_region_code`, `prop_elevation_m`, `prop_classification`.
- 72 observations land (24 per month). 18 rows carry Fahrenheit values under
  `unit_f`; staging converts them exactly (`50F` to `10C`, `59F` to `15C`,
  `68F` to `20C`, `77F` to `25C`) while the other 54 pass through unchanged.
- `monthly_indicators` emits 12 station-month rows of 6 observations each;
  per-station mean temperatures are constant across months (`P1` 13.0,
  `P2` 14.5, `P3` 15.666667, `P4` 14.333333 Celsius) and each station-month
  totals exactly `9.0` mm of precipitation.
- `annual_rollup` emits 4 station-year rows. The sum of monthly aggregates
  equals the direct annual sum for every station (`27.0` mm, `precip_delta`
  = 0), joined against 2026 regional demographics (north: population
  `1,214,000`, median age `38.9`; south: `961,500` / `41.6`).

### Reproducing the pre-revision result with time travel

Because the revision merged in place, the current table shows only the new
population. Nessie keeps every ingest snapshot, so Trino can read the table
as of before the 2026-08-11 run:

```sql
-- find the snapshot id committed just before the revision merge
SELECT snapshot_id, committed_at
FROM iceberg.raw."places_registry$snapshots"
ORDER BY committed_at;

-- reproduce the old result: P3 still reads 120000
SELECT place_id, name, population
FROM iceberg.raw.places_registry
FOR VERSION AS OF <pre_revision_snapshot_id>
WHERE place_id = 'P3';
```

Comparing the time-traveled row with the current table isolates exactly the
revised field - the same one-field diff the test suite proves at fixture
level.

## Expected failures

Each labeled fixture under `generated-data/failures/` breaks one invariant,
proven by `tests/test_public_data_research.py`:

- `observations_orphan_station.csv`: station `PX` does not exist in the
  civic registry; `assert_known_stations` returns a violation naming the
  orphan while the contract itself accepts the rows. On the ingestion asset
  this check is blocking, so an orphan batch fails closed.
- `precip_negative.csv`: a `-1.5` mm reading breaks exactly the numeric
  contract (`precip_mm >= 0`); Pandera rejects the batch at ingest.

The drift month is deliberately not a failure case: July's `pressure_hpa`
column validates under the same contract, and pre-drift months validate too,
because the field is declared optional.

## Schedules

| Schedule | Cron | Job |
|---|---|---|
| civic daily ingestion | `15 6 * * *` | place registry for the day |
| weather monthly ingestion | `0 7 2 * *` | previous month's archive |
| demographics annual ingestion | `0 8 1 2 *` | latest census year |
| research rebuild | `45 7 * * *` | staging through annual rollup |
| weekly reconciliation | `0 6 * * 6` | full WAP pass over every asset |

Asset settings follow source behavior: tight freshness on the daily civic
feed, relaxed monthly/annual freshness on bulk files, merge strategies
everywhere because public publishers restate history, and the coverage gate
as a blocking `quality_checks` entry rather than a downstream warning.

## Profile maturity

Blessed Iceberg stack (MinIO + Nessie + Trino). CI-first: pytest needs no
containers (the replay server runs in-process on port 0, and archives are
read directly from disk), and the live path is deterministic because the
replay API serves generated bytes and zip members are byte-stable.

## Platform requirements and known semantics

Requires phlo-dagster runtime images built after #766 (glibc base), matching
the other examples. Point `CIVIC_API_URL` at any registry-compatible feed to
switch from replay to live mode without touching workflow code. Weather and
demographic assets resolve their natural grain (month/year) from the daily
partition key prefix, so backfills stay daily even though storage partitions
are monthly and annual.
