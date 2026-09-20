# Project templates

`phlo init --list-templates` lists built-in and installed provider templates. The registry merges built-in templates with templates exposed through the `phlo.project_templates` entry point group.

## Current templates

| Template | Packages | Purpose |
| --- | --- | --- |
| `minimal` | `phlo` | Empty project skeleton |
| `basic` | `phlo`, `phlo-dbt` | dbt-ready project |
| `csv-batch` | `phlo`, `phlo-dlt`, `phlo-pandera` | Local CSV ingestion with validation |
| `dbt-medallion` | `phlo`, `phlo-dbt` | Bronze, silver, and gold dbt models |
| `api-ingestion` | `phlo`, `phlo-dlt`, `phlo-pandera` | REST API ingestion |
| `observability-demo` | `phlo`, `phlo-dlt`, `phlo-pandera`, `phlo-otel` | Ingestion with telemetry wiring |
| `sling-replication` | `phlo`, `phlo-sling` | Sling replication starter |

The current command output is:

```text
api-ingestion        REST API ingestion pipeline          phlo, phlo-dlt, phlo-pandera
basic                dbt-ready Phlo project               phlo, phlo-dbt
csv-batch            Local CSV batch pipeline             phlo, phlo-dlt, phlo-pandera
dbt-medallion        Bronze/silver/gold dbt project       phlo, phlo-dbt
minimal              Empty Phlo project                   phlo
observability-demo   Pipeline with telemetry wiring       phlo, phlo-dlt, phlo-pandera, phlo-otel
sling-replication    Sling replication starter            phlo, phlo-sling
```

## minimal

The built-in template writes `phlo.yaml`, `pyproject.toml`, `.env.example`, `.gitignore`, `.phlo/.gitignore`, `README.md`, `AGENTS.md`, `contracts/`, `data/`, `plugins/`, a workflow package skeleton, and a test package skeleton. Run `phlo services init` first, then use `phlo workflow create` to add a workflow.

## basic

The dbt package adds `workflows/transforms/dbt/dbt_project.yml` and the dbt model directory on top of `minimal`. Run `phlo services init`, then use `phlo workflow create` or read [Transform with dbt](../guides/transform-with-dbt.md).

## csv-batch

The DLT package adds `data/events.csv`, `workflows/ingestion/csv/events.py`, and `workflows/schemas/csv.py`. Run `phlo test`, then materialise `dlt_events` through Dagster.

## dbt-medallion

This template adds `bronze/source_events.sql`, `silver/stg_events.sql`, `gold/dim_events.sql`, and `sources.yml` under the dbt models directory. Run `phlo dbt compile` to inspect the generated project, then restart Dagster and read [Transform with dbt](../guides/transform-with-dbt.md).

## api-ingestion

The DLT package adds `workflows/ingestion/api/events.py` and `workflows/schemas/api.py`. Run `phlo test`, then materialise `dlt_events` through Dagster.

## observability-demo

This template builds on `csv-batch` and adds `workflows/ingestion/observability/events.py` plus `phlo-otel`. Run `phlo services init`, then enable the observability profile and read [Add observability](../guides/add-observability.md).

## sling-replication

The Sling package adds `replication/sling.yaml` and a sample `data/events.csv`. Run `phlo sling --help` to inspect the provider integration, then read [Ingest data](../guides/ingest-data.md) for Dagster asset execution.

## First command

List templates before creating a project when you need to compare their generated package sets.

```bash
phlo init --list-templates
phlo init my-project --template minimal
```

The generated README records template-specific next steps.
