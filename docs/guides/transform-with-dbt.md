# Transform with dbt

This guide adds a dbt model to the generated workflow tree, runs it through Phlo, and verifies that the model is exposed as a Dagster asset.

## Before you start

- You have a project created with `phlo init --template dbt-medallion` and a running default stack with Trino.
- `phlo-dbt` is installed, and the source table already exists in the `iceberg` catalog.
- You know whether the model should be a view, table, or incremental model.

## 1. Inspect the dbt project layout

The dbt-medallion template places the dbt project under `workflows/transforms/dbt`. Keep `dbt_project.yml`, `profiles/`, `models/`, and `macros/` inside that directory so the Phlo dbt provider can discover them.

```text
workflows/transforms/dbt/
├── dbt_project.yml
├── profiles/profiles.yml
├── models/
│   ├── sources/sources.yml
│   ├── bronze/
│   ├── silver/
│   └── gold/
└── macros/
```

The template gives you a working profile and medallion model directories. Your first visible result is a dbt project that `phlo dbt` can locate without a second project root.

## 2. Declare the Phlo table as a source

Create `workflows/transforms/dbt/models/sources/events.yml`. dbt's `source()` helper expands the catalog and schema names from this declaration instead of embedding a three-part relation in every model.

```yaml
version: 2

sources:
  - name: raw
    database: iceberg
    schema: raw
    tables:
      - name: events
```

The source node appears in dbt's manifest and becomes the dependency of every model that calls `source('raw', 'events')`.

## 3. Write a model

Create `workflows/transforms/dbt/models/silver/event_summary.sql`. `source()` is for an ingestion table. Use `ref()` when one dbt model depends on another.

```sql
{{ config(materialized='table', schema='silver') }}

select
    event_id,
    name,
    cast(value as integer) as value
from {{ source('raw', 'events') }}
where value is not null
```

The model compiles to a Trino query against `iceberg.raw.events` and writes the result to the configured `silver` schema.

| Setting | Effect |
| --- | --- |
| `source('raw', 'events')` | References the declared Phlo table and records lineage. |
| `ref('other_model')` | References another dbt model and adds a model dependency. |
| `materialized='table'` | Creates a physical table for the model result. |
| `schema='silver'` | Places the model in the target schema configured by the profile. |

## 4. Run dbt through Phlo

Inspect the provider's command group before running a transformation:

```bash
phlo dbt --help
phlo dbt compile
phlo dbt run
phlo dbt test
```

`compile` writes compiled SQL and a manifest, `run` materializes selected models, and `test` executes dbt tests. The command output names the project, selected models, and each completed dbt task.

## 5. Inspect the asset graph

The `phlo-dbt` provider emits model definitions that the Dagster adapter presents as assets. Open `http://localhost:10006` and search for `event_summary`. Its upstream graph includes the source-backed ingestion relation.

```bash
phlo catalog tables
```

The catalog listing includes the model's target relation after a successful run, while Dagster shows the model as an asset with its dbt dependency metadata.

## Verify

Run the model and query the target relation with an explicit Trino catalog:

```bash
phlo dbt run --select event_summary
phlo trino --catalog iceberg
```

```sql
select event_id, name, value from silver.event_summary limit 10;
```

The expected result is a table of transformed rows and a Dagster run marked successful for the dbt asset.

## Related

- [Ingest data](ingest-data.md) for the raw table used by this model.
- [Choose your stack](choose-your-stack.md) for query-engine and transformation packages.
- [Packages](../reference/packages.md) for `phlo-dbt` support details.
