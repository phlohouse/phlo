# What data engineering is

Data engineering turns changing source records into datasets that other people can trust. This post gives you a plain-language map of that work and shows how Phlo connects each responsibility to a concrete part of a project.

## The problem from first principles

A source system records events for its own purpose. A shop records orders so it can fulfil them. A product records clicks so it can improve a feature. Those records are not automatically a dataset that another person can query safely. They can arrive late, change shape, contain duplicates, or describe the same thing with different names.

Data engineering is the work of making those records useful beyond the system that created them. You decide what to collect, how to store it, how to change it into a useful shape, how to detect bad results, and how to explain what happened when a run fails.

Think of a dataset as a prepared meal rather than a bag of ingredients. The ingredients are the source rows. Preparation gives them a known recipe, a serving time, a quality check, and someone responsible for fixing the recipe when an ingredient changes. A data dump has rows. A dataset also has meaning and expectations.

The work has several layers:

- Ingestion extracts records from a source and loads them.
- Storage keeps those records in a table format that can be read consistently.
- Transformation applies business logic such as joining events to customers.
- Quality checks test whether the result is fit for use.
- Orchestration decides what runs, when it runs, and what depends on it.
- Serving gives people and applications a safe way to query the result.
- Observability shows whether the system ran and whether the result is healthy.

You do not need every layer on the first day. You do need to know which layer owns each decision. A source problem should not be fixed by weakening a quality rule. A slow query should not be fixed by deleting the evidence that the query was slow.

The boundary between layers is also a boundary between questions. Ingestion asks whether the source was read and converted. Storage asks whether a reader can find a consistent table state. Transformation asks whether the rows answer a business question. Quality asks whether the result satisfies its contract. Orchestration asks whether the work ran at the expected time and in the expected order. Serving asks whether a consumer can query the result. Observation asks whether someone can see all of those answers later.

That map helps you choose the next investigation. If the source file is missing, a SQL model is not the first place to look. If a model returns the wrong value, restarting MinIO cannot fix the transformation. If a result is right but stale, inspect scheduling and freshness rather than rewriting the schema. A platform becomes easier to operate when each symptom has a first place to look.

The boundary between layers is also a boundary between questions. Ingestion asks whether the source was read and converted. Storage asks whether a reader can find a consistent table state. Transformation asks whether the rows answer a business question. Quality asks whether the result satisfies its contract. Orchestration asks whether the work ran at the expected time and in the expected order. Serving asks whether a consumer can query the result. Observation asks whether someone can see all of those answers later.

That map helps you choose the next investigation. If the source file is missing, a SQL model is not the first place to look. If a model returns the wrong value, restarting MinIO cannot fix the transformation. If a result is right but stale, inspect scheduling and freshness rather than rewriting the schema. A platform becomes easier to operate when each symptom has a first place to look.

## How Phlo approaches it

Phlo gives each layer a package and a visible runtime boundary. The `phlo-dlt` package provides ingestion assets. `phlo-iceberg` provides table storage and `phlo-nessie` provides the catalogue and its branches. `phlo-dbt` turns dbt models into Dagster assets. `phlo-pandera` provides schema validation and quality checks.

Dagster is the run path. Every asset runs as a Dagster run, whether you launch it with `phlo materialize`, `phlo backfill`, a schedule, or the Dagster UI. `phlo-trino` provides query access for inspection and serving. Observatory shows run and platform information, while the optional lineage and telemetry packages add views of dependencies and signals.

This separation gives you a place to put each expectation. A `unique_key` on an ingestion asset describes how duplicate source rows are handled. A `merge_strategy` describes whether rows are inserted or merged. A Pandera schema describes valid columns and values. A Dagster run records which asset and partition ran. A Dataset declaration describes who owns the result and who consumes it.

Phlo also treats correctness, freshness, and cost as a connected trade-off. A result that is correct but arrives too late may still fail its purpose. A result that is fresh but wrong is worse. A result that is correct and fresh but costs more than its value needs a design change. You can call this the reliability triangle.

## Try it

From any shell where Phlo is installed, inspect the top-level command and the available project templates:

```bash
phlo --help
phlo init --list-templates
```

The first command lists the platform entry points. The second lists the built-in project starters, including `csv-batch`, `dbt-medallion`, and `observability-demo`. These commands do not start a service or create a project.

Use the `csv-batch` template for the rest of this series. It gives the ideas a small, repeatable example without requiring a remote source system.

## Mental model to keep

- A source row is an input, not a trustworthy dataset.
- A dataset needs ownership, a schema, freshness expectations, quality rules, and known consumers.
- Each layer should answer one question and expose a signal when it cannot answer it.
- Dagster records the normal execution path for assets.
- Correctness comes before speed, and speed comes before cost optimisation.

## Where this goes wrong

- **The source changes shape.** A schema validation failure identifies the changed field before the new rows reach the published table.
- **The same record arrives twice.** A unique key and merge strategy reveal whether the asset can safely replay the partition.
- **A run finishes but the table is stale.** Freshness checks and asset status show that a successful process did not meet the dataset's timing expectation.
- **A consumer cannot explain a number.** Lineage and the Dagster asset graph show which upstream asset and partition produced the result.
- **A query becomes expensive.** Trino query behaviour and Dagster run durations provide evidence for a partition or model change instead of guesswork.

## Next

Continue with [Your first Phlo project](02-your-first-phlo-project.md) to see where the layers live in a generated project. For the complete hands-on setup, use [Build your first pipeline](../getting-started/first-pipeline.md).
