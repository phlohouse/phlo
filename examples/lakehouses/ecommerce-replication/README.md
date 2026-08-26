# E-commerce Replication lakehouse

A source-style Phlo lakehouse that replicates a small commerce PostgreSQL
database through Sling and exercises every replication mode the platform
supports. It exists to answer one question: do full-refresh, incremental, and
snapshot replications coexist predictably when the source mutates under them?

The project owns its uv environment, deterministic fixtures, workflow
configuration, and local source database. It does not depend on another
example's runtime state.

## What it exercises

| Area | Coverage |
|---|---|
| Source | Local PostgreSQL (`docker-compose.yml`, port 5436) seeded from deterministic CSV fixtures |
| Ingestion | Six `phlo.ingest.sling` assets: one `snapshot` stream, three `incremental` streams (including a composite primary key), two `full-refresh` streams |
| Transforms | Central dbt project: customer dimension (snapshot history collapse), order lifecycle facts, daily revenue mart, payment reconciliation evidence |
| Quality | Composite-key uniqueness, order-line arithmetic and referential integrity, payment reconciliation, watermark monotonicity, labeled invalid fixtures that must fail their named invariant |
| Orchestration | 15-minute incremental job, nightly reference refresh, weekly customer snapshot, daily transforms, weekly full WAP reconciliation; all schedules stopped by default |
| Data plane | Iceberg tables in MinIO via Nessie catalog, Trino query engine, WAP branch promotion |

## Layout

```text
scripts/                              deterministic fixtures and source seeding
workflows/sources/commerce_postgres/  six Sling replication streams
workflows/domains/customers/          customer snapshot contract checks
workflows/domains/orders/             reconciliation and watermark checks
workflows/schemas/                    Pandera contracts for replicated tables
workflows/schedules/                  five Dagster jobs and schedules
workflows/transforms/dbt/             central dbt models, sources, tests
tests/                                fast deterministic contract/failure tests
```

## Run the lakehouse

From this directory:

```bash
uv sync --locked --group dev
uv run python scripts/generate_fixtures.py --scenario update
uv run pytest -q tests
uv run ruff check .
```


```bash
docker compose up -d commerce-postgres
uv run python scripts/seed_postgres.py --stage base
uv run phlo validate-workflow workflows/sources/commerce_postgres/__init__.py
```

Start the platform and replicate:

```bash
uv run phlo services init --force --no-dev
uv run phlo services start --build
uv run phlo doctor
uv run phlo dbt compile
```

Materialize the initial replication through Phlo — not Sling directly:

```bash
uv run phlo materialize sling_commerce_products
uv run phlo materialize sling_commerce_config
uv run phlo materialize sling_commerce_orders
uv run phlo materialize sling_commerce_order_lines
uv run phlo materialize sling_commerce_payments
uv run phlo materialize sling_commerce_customers
```

Then prove that updates replicate without a reload:

```bash
uv run python scripts/seed_postgres.py --stage update   # mutate the source
uv run phlo materialize sling_commerce_orders           # picks up only newer rows
uv run phlo materialize sling_commerce_payments
uv run phlo materialize sling_commerce_customers        # snapshot keeps history
```

And build the marts:

```bash
uv run phlo materialize customer_dimension
uv run phlo materialize order_lifecycle_facts
uv run phlo materialize daily_revenue_mart
uv run phlo materialize payment_reconciliation
```

The source connection defaults to
`postgresql://commerce:commerce@localhost:5436/commerce?sslmode=disable`
(the compose Postgres runs without TLS); override `COMMERCE_SOURCE_URL` to
point elsewhere.

## Expected results (verified end to end)

- Base replication: 200 customers, 150 products, 1,400 orders, 2,786 order
  lines, 1,555 payments, 4 config rows.
- After `--stage update`: incremental runs append exactly the delta - 50 new
  orders and 99 new lines; updated orders/payments arrive as additional
  versions (raw counts 1,506 / 1,612 with 1,450 / 1,605 distinct keys).
- The second customer snapshot run takes raw history to 400 rows while
  `customer_dimension` returns exactly 200 current customers.
- `order_lifecycle_facts` holds 1,450 unique orders;
  `payment_reconciliation` reports `reconciled` for every one of them;
  `daily_revenue_mart` totals 277,417.02 USD gross across 43 partitions.

## Expected failures

- `tests` proves each labeled failure fixture breaks exactly the invariant it
  names: an orphan order line fails referential integrity, an over-payment
  fails reconciliation, a stale customer row fails watermark regression.
- Re-materializing an incremental stream after rewinding its watermark is a
  no-op by design; forcing stale rows through the check raises.

## Profile maturity

Blessed Iceberg stack (MinIO + Nessie + Trino) with Sling as the optional
ingestion provider. The example is CI-first: pytest needs no containers, and
the documented live path is deterministic because both the source state and
the update delta are generated, not recorded.

## Platform requirements and known semantics

Requires phlo-dagster runtime images built after #766 (glibc base): the Sling
CLI upstream publishes glibc-only Linux binaries and cannot execute on the
previous musl-based image. The local source database runs without TLS, so the
source DSN carries `sslmode=disable`.

Sling's Iceberg target is append-only for incremental mode (upstream warning:
"primary-key is ineffective, incremental merge is not yet supported"). New
rows replicate cleanly; updated rows arrive as additional versions. The
central dbt models therefore collapse each stream to its latest version by
`updated_at` before aggregating - read-time CDC semantics. Snapshot-mode
customers intentionally accumulate full history in the raw table.
