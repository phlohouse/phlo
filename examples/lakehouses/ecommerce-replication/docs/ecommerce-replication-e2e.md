# E-commerce Replication: end-to-end verification record

Date: 2026-08-23. Base: phlo main at 2beae89f69 (includes #766, the glibc
runtime image fix). Host: Apple M2 Pro (Darwin/arm64), Docker 29.4.0.

## Outcome

The full documented end-to-end scenario passes: six Sling replication streams
materialized through Dagster onto Iceberg (MinIO + Nessie + Trino), source
updates replicated incrementally without a reload, and all four dbt models
built with their tests passing through WAP promotion.

## Environment notes

- The example pins `phlo-dagster` to the merged commit `2beae89f69` so the
  staged runtime image is glibc-based. Images built before #766 cannot execute
  the Sling CLI (musl vs glibc; `gcompat` does not help - `fcntl64` missing).
- The compose Postgres runs without TLS, so the source DSN carries
  `?sslmode=disable`. Without it every replication fails with
  `pq: SSL is not enabled on the server`.
- `services init` renders `.phlo/.env` from `phlo.yaml`; after changing an env
  value you must re-run init and restart, otherwise containers keep serving
  the previous DSN.
- Sling downloads its CLI binary on first execution per container
  (~250 MB into `$HOME/.sling`). First runs are slow; later ones reuse it.

## Verified results

| Check | Result |
|---|---|
| Deterministic fixtures | Byte-identical across runs; generator enforces the watermark invariant on update rows |
| pytest contract suite | 10 passed (modes/PKs/contracts per asset, schedules, dbt model evidence, labeled failure cases) |
| Source seeding (typed DDL) | base: 200 / 150 / 1400 / 2786 / 1555 / 4 rows; update stage applies exactly 25+56+7 upserts and 50+99+50 inserts |
| Platform stack | `phlo doctor` 14 ok; dagster, dagster-daemon, minio, nessie, postgres, trino healthy |
| Asset discovery | 10 assets registered: six sling_* streams plus four dbt models |
| Initial replication (all six assets, Dagster/WAP) | SUCCESS x6; raw counts exactly match fixtures; types preserved (decimal, timestamptz, boolean) |
| Delta replication | orders raw 1506 rows / 1450 distinct keys (+50 new, +56 updated versions); lines 2885; payments 1612 rows / 1605 distinct (+50 new, +7 corrected versions); customers snapshot history 400 rows |
| customer_dimension | 200 current customers from 400 accumulated snapshot rows |
| order_lifecycle_facts | 1,450 unique orders (latest-version-wins over append-only incremental) |
| daily_revenue_mart | 43 partitions; gross revenue 277,417.02 USD; order counts sum to 1,450 |
| payment_reconciliation | `reconciled` for all 1,450 orders; zero over/under-paid or delivered-unpaid flags |
| dbt tests via Dagster asset checks | unique/not-null/relationships/accepted-values pass |

## Findings surfaced by this example

1. **Fixed in #766**: the Alpine-based phlo-dagster image could not execute
   the glibc-only Sling binary. This was a hard blocker for every
   `phlo.ingest.sling` asset.
2. **Sling upstream**: incremental mode against an Iceberg target is
   append-only ("primary-key is ineffective, incremental merge is not yet
   supported"). Updated source rows arrive as extra versions; consumers must
   deduplicate latest-version-wins (the central dbt models do). A native merge
   would remove the dedup layer.
3. **No `_phlo_ingested_at` lineage columns on the Sling path** (they exist for
   DLT ingestion), so model dedup keys off the source `updated_at` instead.
4. WAP run isolation means a model that `ref()`s another model requires the
   upstream to be promoted to the main branch first; first-time materialization
   must follow dependency order one asset at a time.

## Reproducing

```bash
cd examples/lakehouses/ecommerce-replication
uv sync --locked --group dev
uv run python scripts/generate_fixtures.py --scenario update
docker compose up -d commerce-postgres
uv run python scripts/seed_postgres.py --stage base

uv run phlo services init --force --no-dev
uv run phlo services start --build
uv run phlo doctor

for a in sling_commerce_products sling_commerce_config sling_commerce_orders \
         sling_commerce_order_lines sling_commerce_payments sling_commerce_customers;
  do uv run phlo materialize $a; done

uv run python scripts/seed_postgres.py --stage update
for a in sling_commerce_orders sling_commerce_order_lines sling_commerce_payments \
         sling_commerce_customers;
  do uv run phlo materialize $a; done

# dependency order matters across WAP branches:
uv run phlo materialize customer_dimension --partition 2026-07-20
uv run phlo materialize order_lifecycle_facts --partition 2026-07-20
uv run phlo materialize payment_reconciliation --partition 2026-07-20
uv run phlo materialize daily_revenue_mart --partition 2026-07-20
```
