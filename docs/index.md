# Phlo documentation

Phlo is a Python framework for building a lakehouse. You write ingestion, quality, and transformation assets as decorated Python functions in one project. The `phlo` CLI generates the local stack, runs the assets through an orchestrator, lands tables in versioned storage, and shows you what happened in Observatory.

The core stays small. Everything else, from Dagster and dlt to Iceberg, Nessie, Trino, and MinIO, arrives as an installable `phlo-*` package that plugs into the CLI and the runtime.

## Start here

If you have never run Phlo, follow these two pages in order:

1. [Install Phlo](getting-started/install.md) sets up Python, Docker, and the `phlo` command.
2. [Build your first pipeline](getting-started/first-pipeline.md) creates a project, starts the stack, and materialises a table in about ten minutes.

If you want to understand the ideas behind that workflow, follow [Learn Phlo from first principles](learn/index.md). The series builds on the same project and explains ingestion, storage, orchestration, quality, observability, and extension design.

## Understand how Phlo works

Read these when you want the reasoning behind the design, not steps:

- [How Phlo works](concepts/how-phlo-works.md) explains what your project owns, what the runtime generates, and how an asset becomes a table.
- [Write-audit-publish](concepts/write-audit-publish.md) explains how Phlo isolates each run on a catalog branch and promotes only runs that pass their checks.
- [Plugins and capabilities](concepts/plugins-and-capabilities.md) explains how packages extend the CLI, the stack, and Observatory.
- [Assets, partitions, and schedules](concepts/assets-partitions-and-schedules.md) explains Dagster assets, partition runs, selectors, schedules, and sensors.
- [Governance and datasets](concepts/governance-and-datasets.md) explains contracts, readiness metadata, Dataset projections, and authorised transitions.
- [Evidence, audit, and compliance](concepts/evidence-audit-and-compliance.md) explains run evidence, audit records, evidence packs, and regulated mode.

## Get something done

Each guide solves one problem and assumes you have finished the first pipeline:

| Goal | Guide |
| --- | --- |
| Load data from an API, a file, or a database | [Ingest data](guides/ingest-data.md) |
| Stop bad rows before they reach a published table | [Add quality checks](guides/add-quality-checks.md) |
| Model bronze, silver, and gold layers | [Transform with dbt](guides/transform-with-dbt.md) |
| Change a table's columns without breaking consumers | [Evolve a schema](guides/evolve-a-schema.md) |
| Swap storage, catalog, query engine, or ingestion tool | [Choose your stack](guides/choose-your-stack.md) |
| Serve tables to apps, analysts, and BI tools | [Expose data](guides/expose-data.md) |
| Add authentication, per-service credentials, and audit logs | [Secure the stack](guides/secure-the-stack.md) |
| Read logs, trace lineage, and fix a failing run | [Monitor and debug](guides/monitor-and-debug.md) |
| Diagnose a `PHLO-` error code | [Troubleshoot Phlo errors](guides/troubleshoot-errors.md) |
| Test assets locally and in CI | [Test a project](guides/test-a-project.md) |
| Add a command, service, or UI panel to Phlo | [Write a plugin](guides/write-a-plugin.md) |
| Prepare a Compose deployment for production | [Run in production](guides/run-in-production.md) |
| Govern a published table | [Govern a dataset](guides/govern-a-dataset.md) |
| Collect and verify evidence | [Collect evidence](guides/collect-evidence.md) |
| Add telemetry and alerts | [Add observability](guides/add-observability.md) |
| Run Dagster on the host | [Develop natively](guides/develop-natively.md) |
| Choose a maintenance or recovery runbook | [Maintain and recover](guides/maintain-and-recover.md) |

## Look something up

Reference pages describe what exists. They do not teach:

- [CLI](reference/cli.md): the top-level `phlo` commands and their subcommands. Run `phlo <command> --help` for all options on the installed version.
- [Configuration](reference/configuration.md): `phlo.yaml`, environment-file precedence, and core and default-stack variables.
- [Project layout](reference/project-layout.md): the files in a project and what generates them.
- [Python API](reference/python-api.md): the decorators and helpers under `import phlo`.
- [Plugin API](reference/plugin-api.md): entry points, hooks, `service.yaml`, and extension manifests.
- [Quality checks](reference/quality-checks.md): every built-in check and its parameters.
- [Packages](reference/packages.md): what each `phlo-*` package adds and its support tier.
- [Auth and access](reference/auth-and-access.md): principals, roles, and route guards.
- [Errors](reference/errors.md): every `PHLO-` error code, its cause, and its fix.
- [Glossary](reference/glossary.md): the terms these docs use.
- [Templates](reference/templates.md): project starters and generated files.
- [Hooks and events](reference/hooks-and-events.md): event types, filters, and dispatch semantics.

The hand-maintained [Python API reference](reference/python-api.md) covers the public decorators and helpers. The current build does not publish the generated `python-reference` route.

## Architecture records

[Architecture](architecture/index.md) holds the decision log and the regulated surface inventory. Read them when you need to know why something is the way it is.

## Project status

Phlo is alpha. The local workflow is exercised in CI. Public APIs, package contracts, and the project layout can change before 1.0. The machine-readable support boundary in `registry/support/v1.json` states which packages are blessed, preview, or development-only. Pin exact versions in production.
