# Transformation with dbt

Transformation turns ingested records into the shape that people use for analysis or applications. This post explains how SQL models fit into a Phlo asset graph, why bronze, silver, and gold are useful labels rather than rules, and how dbt tests become visible Dagster checks.

## The problem from first principles

Raw data preserves what a source sent. Consumers usually need something else: consistent names, typed values, joins, filters, and business definitions. Embedding every transformation in ingestion code makes those definitions difficult to review and reuse.

SQL is a useful transformation language because many people can read it, query engines can execute it close to the data, and a model can declare its dependencies. A model also needs tests. A calculated value that looks plausible can still contain null keys, duplicate rows, or an unexpected range.

The separation between ingestion and transformation gives each stage a clear responsibility. Ingestion preserves source facts and source identity. A model can then select columns, cast values, filter incomplete records, or combine relations without changing the original evidence. When a business definition changes, you can review the SQL and its tests as a focused change.

Teams often describe layers as bronze, silver, and gold. Bronze is close to the source, silver is cleaned or conformed, and gold is shaped for a consumer or decision. The labels help you discuss distance from the source. They do not require every project to have exactly three layers.

## How Phlo approaches it

The `phlo-dbt` package discovers the dbt project under `workflows/transforms/dbt`. It reads the dbt manifest and turns models, seeds, and snapshots into Dagster assets. A dbt `source()` can carry `phlo_asset_key` metadata so the source relation links to an existing Phlo asset. A dbt `ref()` adds a model dependency.

The asset key for the example model is `event_summary`. The model path supplies its asset group, so a model under `models/silver/` belongs to the `silver` group. dbt tests become Dagster asset checks on the generated asset.

The source binding is the point where the two projects meet. dbt knows the relation as `raw.events`, while Phlo knows the producing asset as `dlt_events`. `phlo_asset_key` preserves that relationship in the graph, so the model is not an isolated SQL statement. Its upstream run and partition remain visible to the consumer.

The model remains understandable when the platform is not running. You can review its SQL, source declaration, and tests in a code change. When Dagster runs it, the same files become a recorded asset with upstream context and checks. This gives a reviewer both a familiar transformation language and a platform-level account of its result.

This is also why a transformation model should have a narrow purpose. A model that cleans one relation is easier to test than a model that combines ingestion, business logic, and serving decisions. Keep the source relation and the consumer result visible in the SQL, then let the asset graph explain how the model fits with the rest of the project.

The normal execution path is Dagster. `phlo dbt compile` is useful for local SQL inspection. `phlo dbt run` and `phlo dbt test` call dbt directly for debugging and do not create the normal Dagster run record, lineage, or asset-check results. Use `phlo materialize` to populate the lakehouse.

## Try it

Add the following model from [Transform with dbt](../guides/transform-with-dbt.md) at `workflows/transforms/dbt/models/silver/event_summary.sql`:

```sql
{{ config(materialized='table') }}

select
    event_id,
    name,
    cast(value as integer) as value
from {{ source('raw', 'events') }}
where value is not null
```

The source declaration binds the dbt source to the ingestion asset:

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

Add the dbt tests from the guide:

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

Compile the project for local SQL inspection:

```bash
phlo dbt compile
```

Restart Dagster so it reads the compiled manifest:

```bash
phlo services restart --service dagster
```

Materialise the model through Dagster:

```bash
phlo materialize event_summary --partition 2025-01-15
```

Open the Dagster UI and inspect `event_summary`. You should see `dlt_events` upstream and the two asset checks attached to the model.

## Mental model to keep

- dbt expresses transformation logic in SQL.
- `source()` connects a model to an upstream relation.
- `ref()` connects one dbt model to another.
- The asset graph makes SQL dependencies visible to Dagster.
- Compile is inspection. Materialisation is the normal run path.
- dbt tests become checks that travel with the asset run.

## Where this goes wrong

- **The model is not discovered.** Check the project path and restart Dagster after compilation.
- **Lineage starts at a table instead of an ingestion asset.** Add the verified `phlo_asset_key` metadata to the dbt source.
- **A SQL test fails.** Open the model's Dagster asset check and inspect the failing partition.
- **A direct dbt run changes the table but history is incomplete.** Use `phlo materialize` for normal execution.
- **A model layer becomes a rule instead of a vocabulary.** Choose names that explain the consumer contract rather than adding layers without a purpose.

## Next

Continue with [Quality with Pandera](07-quality-with-pandera.md) to compare ingestion validation with table-level checks. Use [Transform with dbt](../guides/transform-with-dbt.md) for the full file-by-file procedure and [Packages](../reference/packages.md) for `phlo-dbt` support details.
