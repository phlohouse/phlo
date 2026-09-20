# Transform with dbt

This guide adds a dbt model to a Phlo project, materialises it as a Dagster asset with `phlo materialize`, and verifies the result in the catalog.

In Phlo, dbt models are not run with the dbt CLI. `phlo-dbt` reads the dbt manifest and turns every model, seed, and snapshot into a Dagster asset. You materialise those assets the same way you materialise an ingestion asset, and dbt tests run as asset checks on the same run.

## Before you start

- You have the `my-lakehouse` project from [Your first pipeline](../getting-started/first-pipeline.md), the stack is running, and `dlt_events` has been materialised so `iceberg.raw.events` exists.
- You know whether the model should be a view, table, or incremental model.

If you prefer to start from a dbt-first project, `phlo init --template dbt-medallion` scaffolds the same layout with sample bronze, silver, and gold models but no ingestion asset.

## 1. Add the dbt project

Add `phlo-dbt` to the project dependencies in `pyproject.toml` and install it:

```bash
uv pip install -e .
```

Create `workflows/transforms/dbt/dbt_project.yml`. `phlo-dbt` discovers a dbt project at this path without further configuration.

```yaml
name: my_lakehouse
version: 1.0.0
config-version: 2
profile: phlo
model-paths: ["models"]

models:
  my_lakehouse:
    +materialized: table
```

You do not write a `profiles.yml`. `phlo-dbt` generates the `phlo` profile from your Trino settings when it compiles the project.

## 2. Declare the Phlo table as a source

Create `workflows/transforms/dbt/models/sources.yml` with a source that points at the ingestion table. The `phlo_asset_key` entry binds the dbt source to the `dlt_events` Dagster asset so the model depends on the ingestion asset in the asset graph.

```yaml
version: 2

sources:
  - name: raw
    database: iceberg
    schema: raw
    tables:
      - name: events
        meta:
          phlo_asset_key: dlt_events
```

Without `phlo_asset_key`, the provider derives the key `raw.events` and Dagster shows the model with an external dependency instead of a link to `dlt_events`.

## 3. Write a model

Create `workflows/transforms/dbt/models/silver/event_summary.sql`. Use `source()` for an ingestion table and `ref()` when one dbt model depends on another.

```sql
{{ config(materialized='table') }}

select
    event_id,
    name,
    cast(value as integer) as value
from {{ source('raw', 'events') }}
where value is not null
```

The model compiles to a Trino query against `iceberg.raw.events` and writes the result to the profile's default schema, `raw`. Set `DBT_QUERY_SCHEMA` to change the default schema for every model.

| Setting | Effect |
| --- | --- |
| `source('raw', 'events')` | References the declared Phlo table and records lineage to `dlt_events`. |
| `ref('other_model')` | References another dbt model and adds an asset dependency. |
| `materialized='table'` | Creates a physical table for the model result. |
| `materialized='view'` | Creates a view instead of a table. |

The asset key is the model name, `event_summary`. The asset group is inferred from the model path, so a model under `models/silver/` lands in the `silver` group.

## 4. Add a dbt test

Create `workflows/transforms/dbt/models/silver/schema.yml`. Each dbt test becomes a Dagster asset check on `event_summary`.

```yaml
version: 2

models:
  - name: event_summary
    columns:
      - name: event_id
        tests:
          - not_null
          - unique
```

## 5. Compile and reload the asset graph

Compile the project so the manifest exists, then restart Dagster so it picks up the new assets:

```bash
phlo dbt compile
phlo services restart --service dagster
```

Open `http://localhost:10006` and search for `event_summary`. The asset has `dlt_events` upstream and two asset checks.

`phlo dbt compile` only parses the project. It does not create tables. Use `phlo dbt run` and `phlo dbt test` for local debugging of SQL. Do not use them to populate the lakehouse, because runs made outside Dagster leave no run record, no lineage, and no asset-check results.

## 6. Materialise the model

dbt assets are daily partitioned, like ingestion assets. Materialise the model for the same partition as the source data:

```bash
phlo materialize event_summary --partition 2025-01-15
```

The run compiles the model, executes it through Trino, and evaluates the `not_null` and `unique` checks. Dagster records the materialisation, the check results, and the upstream link to `dlt_events`.

## Verify

Confirm the target relation exists and query it with an explicit Trino catalog:

```bash
phlo catalog tables
phlo trino --catalog iceberg
```

```sql
select event_id, name, value from raw.event_summary limit 10;
```

The expected result is a table of transformed rows, a successful Dagster run for `event_summary`, and two passing asset checks.

## Related

- [Ingest data](ingest-data.md) for the raw table used by this model.
- [Add quality checks](add-quality-checks.md) for checks on ingestion assets.
- [Choose your stack](choose-your-stack.md) for query-engine and transformation packages.
- [Packages](../reference/packages.md) for `phlo-dbt` support details.
