# Retail Files lakehouse

A production-shaped, network-independent retail lakehouse built as a standalone
Phlo consumer. It ingests four file formats, validates five independently
configured assets, builds seven dbt models, and promotes successful writes from
isolated Nessie branches through WAP.

The project owns its uv environment, deterministic fixtures, workflow
configuration, and local lakehouse services. It does not depend on another
example's runtime state.

Read the [end-to-end case study](docs/retail-files-e2e.md) for the verified
table counts, WAP lifecycle evidence, native dbt checks, and Dagster screenshots.

## What it exercises

| Area | Coverage |
|---|---|
| Files | 750 per-store/day CSV files, JSON reference data, 375,000 NDJSON inventory snapshots, and a Parquet archive |
| Ingestion | Five `phlo.ingest.dlt` assets; merge and append modes; strict Pandera validation; stable keys; retries, timeouts, freshness, owners, consumers, and SLAs |
| Transforms | Product/store dimensions, sales facts, append-ledger inventory deduplication, daily store revenue, category performance, and stockout/reorder outputs |
| Quality | Missing-file completeness, duplicate and reference failures, sales arithmetic, accepted values, inventory bounds, dbt uniqueness/not-null/relationship checks |
| Orchestration | Daily partitions, a sequential WAP backfill, five jobs, and five stopped-by-default schedules with hourly/daily/weekly cadences |
| Data plane | Iceberg tables in MinIO, Nessie branches, Trino query catalogs, Dagster checks, and WAP promotion/cleanup |

The default generator creates 25 stores × 500 products × 30 days × 80 sales
lines per store/day: 60,000 sales lines in 750 CSV files and 375,000 inventory
snapshots. `--scale test` is deliberately small and is used only by pytest.

## Layout

```text
scripts/                         deterministic fixtures and optional diagnostics
workflows/ingestion/retail/      five decorated file-ingestion assets
workflows/schemas/               Pandera data contracts
workflows/quality/               cross-file business checks
workflows/schedules/             five Dagster jobs and schedules
workflows/transforms/dbt/        seven models and fifteen dbt tests
tests/                           fast deterministic contract/failure tests
```

## Run the lakehouse

From this directory:

```bash
uv sync --group dev
uv run python scripts/generate_fixtures.py --scale default
uv run --with pytest pytest -q tests
uv run --with ruff ruff check .
uv run phlo validate-workflow workflows/ingestion/retail/files.py

uv run phlo services init --force --no-dev
uv run phlo services start --build
uv run phlo doctor
uv run phlo dbt compile
```

Materialize the first daily partition through Phlo—not DLT or dbt directly:

```bash
uv run phlo materialize dlt_retail_products --partition 2025-01-01
uv run phlo materialize dlt_retail_stores --partition 2025-01-01
uv run phlo materialize dlt_retail_promotions --partition 2025-01-01
uv run phlo materialize dlt_retail_inventory --partition 2025-01-01
uv run phlo materialize dlt_retail_sales_lines --partition 2025-01-01

uv run phlo materialize product_dimension --partition 2025-01-01
uv run phlo materialize store_dimension --partition 2025-01-01
uv run phlo materialize sales_facts --partition 2025-01-01
uv run phlo materialize inventory_balances --partition 2025-01-01
uv run phlo materialize daily_store_mart --partition 2025-01-01
uv run phlo materialize product_category_performance --partition 2025-01-01
uv run phlo materialize stockout_reorder --partition 2025-01-01
```

Each command returns a logical run ID and Dagster run ID. A successful run has
a `.phlo/wap-reports/<logical-run-id>.json` report with `status: promoted`, a
changed `target_hash_after`, and `source_deleted: true`.

Exercise sequential lifecycle completion across two partitions:

```bash
uv run phlo backfill dlt_retail_sales_lines \
  --start-date 2025-01-01 \
  --end-date 2025-01-02 \
  --parallel 1
```

The second report's `target_hash_before` must equal the first report's
`target_hash_after`; both reports must be promoted before the command succeeds.

## Expected catalog results

```bash
uv run phlo catalog tables
uv run phlo trino --execute 'SELECT count(*) FROM iceberg.raw.retail_sales_lines'
uv run phlo trino --execute 'SELECT count(*) FROM iceberg.raw.inventory_balances'
uv run phlo trino --execute 'SELECT * FROM iceberg.raw.daily_store_mart ORDER BY sales_date, store_id LIMIT 5'
```

After the two-day sales backfill and one inventory partition, a fresh stack has
12 tables. Representative counts are 500 products, 25 stores, 4,000 sales
facts, 12,500 deduplicated inventory balances, 50 store/day mart rows, 120
category-performance rows, and 12,500 reorder rows. For 2025-01-01 store S001,
the deterministic mart has 80 lines, 40 transactions, gross amount 6,100.80,
discount 62.84, tax 483.03, and net amount 6,520.99.

## Expected failures

- Removing one expected store CSV makes ingestion fail before staging.
- Fixtures under `generated-data/failures/` cover duplicate lines, unknown
  products, bad arithmetic, and malformed NDJSON.
- Raw inventory is intentionally append-only. Replaying a partition can create
  duplicate raw snapshots; `inventory_balances` ranks by Phlo ingestion metadata
  and keeps the newest stable snapshot ID. Removing that deduplication makes its
  blocking dbt uniqueness check fail and prevents WAP promotion.
- A terminal failed Dagster run updates its durable WAP report to `failed` with
  `failure_reason: dagster_run_failed`; its ref remains available for the normal
  audit-retention cleanup policy and published data is unchanged.
- dbt relationship tests are owned by `sales_facts`; they are emitted as five
  native Dagster check evaluations when that asset runs, not when either
  dimension runs.

Stop the stack when finished:

```bash
uv run phlo services stop
```

`scripts/materialize.py` and direct dbt commands are optional DuckDB/provider
diagnostics only. They are not end-to-end completion evidence.
