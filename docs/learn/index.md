# Learn Phlo from first principles

This series explains the ideas behind a reliable data platform before it asks you to learn its tools. You will build one small `csv-batch` project as you move from raw records to an observable, tested, versioned dataset.

The series is for backend developers, analysts, product people, students, and anyone who wants to understand why data systems need more than a script that copies rows. Each post stands alone, but the examples use the same project, asset, and partition so that each idea has somewhere concrete to land.

## What you will build

You will start with the `csv-batch` template and its `dlt_events` asset. The asset reads two CSV records, validates them with Pandera, and writes the `2025-01-15` partition to an Iceberg table in the Nessie catalog. Later posts add a dbt model, quality checks, schema changes, operational signals, and an extension boundary.

## Before you start

Install Phlo by following [Install Phlo](../getting-started/install.md). You need Python, Docker, and a shell where the `phlo` command is available.

Create the tutorial project once by following [Build your first pipeline](../getting-started/first-pipeline.md). Stop after you have materialised `dlt_events` for `2025-01-15`. Return to this series when that project is ready.

## Follow the series

| Post | Focus | Reading time |
| --- | --- | ---: |
| 1. [What data engineering is](01-what-data-engineering-is.md) | How raw records become trustworthy datasets | 4 min |
| 2. [Your first Phlo project](02-your-first-phlo-project.md) | Project files, services, assets, and the first run | 4 min |
| 3. [Ingestion with dlt](03-ingestion-with-dlt.md) | Extracting, normalising, partitioning, and merging data | 4 min |
| 4. [Tables with Iceberg and Nessie](04-tables-with-iceberg-and-nessie.md) | Table snapshots, catalogues, branches, and publication | 4 min |
| 5. [Orchestration with Dagster](05-orchestration-with-dagster.md) | Asset graphs, runs, schedules, sensors, and checks | 4 min |
| 6. [Transformation with dbt](06-transformation-with-dbt.md) | SQL models, dependencies, and transformation checks | 4 min |
| 7. [Quality with Pandera](07-quality-with-pandera.md) | Contracts, blocking checks, warnings, and evidence | 4 min |
| 8. [Schema evolution and contracts](08-schema-evolution-and-contracts.md) | Changing data without surprising consumers | 4 min |
| 9. [Observability](09-observability.md) | Status, logs, metrics, lineage, evidence, and alerts | 4 min |
| 10. [Incidents and debugging](10-incidents-and-debugging.md) | Triage, recovery, rollback, and learning | 4 min |
| 11. [Performance and cost](11-performance-and-cost.md) | Choosing the next optimisation from evidence | 4 min |
| 12. [Extending Phlo](12-extending-phlo.md) | Plugins, capabilities, hooks, and Observatory | 4 min |

The reading times use the post word count divided by 200 and rounded to the nearest two minutes. They are guides rather than promises.

## How to use the examples

Run commands from the root of your tutorial project. The examples use a completed partition date because a daily partition for today remains open until the day is complete.

The normal execution path is Dagster. `phlo materialize` and `phlo backfill` launch Dagster runs. Provider commands such as `phlo dbt` and `phlo sling` are useful for inspection and debugging, but they do not create the normal Dagster run record.

## Next

Start with [What data engineering is](01-what-data-engineering-is.md), or return to [Build your first pipeline](../getting-started/first-pipeline.md) if you have not created the tutorial project.
