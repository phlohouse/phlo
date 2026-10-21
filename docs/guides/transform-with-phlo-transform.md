# Transform with Phlo Transform

This guide adds a native [Phlo Transform](https://github.com/phlohouse/phlo-transform) workspace to a Phlo project, materialises its models as Dagster assets with `phlo materialize`, and switches an existing dbt project onto the same asset graph without changing ingestion, schedules, or asset keys.

Phlo Transform is a workspace-native SQL compiler and engine. Unlike dbt, it infers dependencies from SQL relations, stores content-addressed state per model, and treats planning, tests, lineage, and promotion as engine primitives. A dbt project remains useful as a migration source: the engine ships a one-way translator and reports every resource as `CLEAN`, `REVIEW`, or `UNSUPPORTED`.

## Prerequisites

- A Phlo project with `phlo-dagster` and a query engine (`phlo-trino`).
- `phlo-transform` in the project dependencies.
- The engine binary. Install the pinned portable release into the project:

```bash
uv add phlo-transform
uv run phlo transform install
```

`phlo transform install` verifies the release checksum and writes `.phlo/bin/phlo-transform`. If you build the engine from source, put `phlo-transform` on `PATH` or set `PHLO_TRANSFORM_BIN`.

## 1. Create a workspace

A workspace is a directory containing `phlo.toml`. The default locations are `transforms/`, `workflows/transforms/`, or any `phlo.toml` below them. Set `PHLO_TRANSFORM_ROOT` in `phlo.yaml` when you keep it elsewhere:

```yaml
env:
  PHLO_TRANSFORM_ROOT: workflows/transforms/native
```

```text
workflows/transforms/native/
  phlo.toml
  transforms/stg_orders.sql
  transforms/orders.sql
  tests/generated/orders_id_not_null.sql
```

```toml
[transform]
default_namespace = "analytics"
default_materialization = "table"
```

`transforms/stg_orders.sql`:

```sql
select order_id, customer_id, total_amount
from raw.orders
```

`transforms/orders.sql`:

```sql
select customer_id, count(*) as order_count, sum(total_amount) as revenue
from analytics.stg_orders
group by customer_id
```

Relations create dependencies; no `ref()` or model YAML is required. `phlo-transform check` compiles the graph and reports diagnostics.

## 2. Bind workspace sources to ingestion assets

The engine resolves `raw.orders` as a physical relation. Phlo also needs the asset that produces it, otherwise materializing the transform would not wait for ingestion and lineage would stop at the source. Declare the binding in the workspace:

```toml
[phlo_transform.sources]
"raw.orders" = "sling_orders"
```

This provider-owned table is ignored by the engine. It keeps the ingestion to transform edge when the native workspace replaces a dbt project, and `phlo transform status` continues to report the same asset counts.

## 3. Verify locally, then materialise

```bash
phlo transform check
phlo transform plan --select analytics.orders
phlo transform run --select analytics.orders --adapter duckdb
phlo transform test --select analytics.orders --adapter duckdb
```

The DuckDB adapter runs in-process, so this needs no services. To target the project's Trino:

```bash
phlo transform run --select analytics.orders
```

In orchestrated runs the provider resolves the Trino endpoint, catalog, and runtime ref from the active query-engine capability; a non-main ref is provisioned through `RefQueryCatalogManager` before the engine runs.

Materialise the assets through Dagster:

```bash
phlo materialize stg_orders
phlo materialize orders
```

Each model becomes one asset. The engine's run report supplies the materialization metadata, and each workspace test becomes an asset check on the model it targets. A failing model or test fails the asset run.

## 4. Switch an existing dbt project

Keep the dbt project where it is and translate it into a native workspace:

```bash
phlo transform translate --from dbt --check
phlo transform translate --from dbt --out workflows/transforms/native
```

`--check` writes nothing and classifies every model, source, and test. `--out` writes the smallest equivalent native workspace; `--overwrite` replaces existing files and `--verify` compile-checks the result. The translation manifest lands at `.phlo/migration/dbt-translation.json`.

Then carry the source bindings across. If the dbt sources already declare `meta.phlo_asset_key`, the binding is the same value; otherwise derive it from the ingestion asset name.

## 5. Choose the provider

A project can carry both representations. Select which one owns the transform graph:

```bash
phlo transform use phlo-transform
phlo services restart dagster

phlo transform status
phlo transform use dbt
phlo transform use --unset
```

`phlo transform use` validates that the provider is installed and writes `.phlo/transformation-provider`. `PHLO_TRANSFORMATION_PROVIDER` overrides the marker for one process. With neither set, dbt owns the graph when a dbt project exists, and the native workspace owns it otherwise.

Use the native asset-key shape to keep keys stable across the switch. The default `leaf` mode emits the last dotted segment (`stg_orders`), which is the dbt model name; set `PHLO_TRANSFORM_ASSET_KEY=logical` to keep `analytics.stg_orders` instead. Schedules and downstream assets that reference model names keep working.

## 6. Inspect the transform graph in the Observatory

Both providers publish the same provider-neutral metadata to Dagster, so the Observatory experience survives a switch:

- The **Transform** page shows the active engine (`dbt` or `phlo-transform`) and lists the workspace, every compiled model, its materialization, target, content-hash version, blocking tests, sources, and last run.
- Each transformation asset's **Overview** tab has a Transform card with its engine, model identity, and last run result.
- `GET /api/v1/transform?env=<env>` returns the same inventory for automation and dashboards, including the active engine.

Start the Observatory alongside the project and open **Transform** in the navigation. The page reports an inactive provider with the `phlo transform use phlo-transform` command rather than an empty table.

## 7. Prove parity before switching

Run both engines against identical raw data and compare every table. The [Customer 360 transform example](../../examples/lakehouses/customer360-transform/) does this with dbt-duckdb and DuckDB:

```bash
uv run phlo transform install
uv sync --group dev
uv run python scripts/verify_parity.py
```

The script prints row counts and unmatched rows per table, then exits non-zero if any table differs, including column types.

## Troubleshooting

- `no phlo-transform workspace found`: set `PHLO_TRANSFORM_ROOT` or create `phlo.toml`.
- `phlo-transform executable is missing`: run `phlo transform install`, or set `PHLO_TRANSFORM_BIN`.
- `multiple phlo-transform workspaces found`: keep one workspace per project, or point `PHLO_TRANSFORM_ROOT` at the intended one.
- `asset key collision`: two models share a leaf name. Set `PHLO_TRANSFORM_ASSET_KEY=logical`, or rename a model.
- The orchestrated graph did not change after `phlo transform use`: restart the Dagster service so definitions reload.
