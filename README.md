<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/phlohouse/phlo/main/.github/readme-header-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/phlohouse/phlo/main/.github/readme-header.png">
  <img src="https://raw.githubusercontent.com/phlohouse/phlo/main/.github/readme-header.png" alt="Phlo" width="900">
</picture>

# Phlo

[![PyPI](https://img.shields.io/pypi/v/phlo.svg)](https://pypi.org/project/phlo/)
[![Python](https://img.shields.io/pypi/pyversions/phlo.svg)](https://pypi.org/project/phlo/)
[![CI](https://github.com/phlohouse/phlo/actions/workflows/ci.yml/badge.svg)](https://github.com/phlohouse/phlo/actions/workflows/ci.yml)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#project-status)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/phlohouse/phlo)


**The Pythonic lakehouse framework.** One Python project to define, run, validate, and inspect lakehouse pipelines.

Phlo is the framework and plugin runtime that ties together familiar lakehouse tools — Dagster, dlt, Sling, dbt, Pandera, Iceberg, Delta, Nessie, Trino, MinIO, and more — behind a single CLI and a coherent product surface called **Observatory**.

---

## Why Phlo

Most lakehouse projects start in Python and quickly spill into YAML, Compose files, orchestration config, catalog setup, quality checks, and a pile of glue scripts and duplicated config. Phlo keeps those pieces in one project.

Use the `phlo` CLI to create a project, start the local stack, materialize assets, run quality checks, follow logs, and inspect what happened. Add provider packages when you need them: Dagster for orchestration, dlt or Sling for ingestion, dbt for transforms, Iceberg or Delta for tables, Trino for query, and Observatory for a UI to inspect assets, tables, lineage, quality, services, and logs.

## What a Phlo asset looks like

A Phlo asset is ordinary Python with lakehouse metadata attached:

```python
from pathlib import Path

import dlt
import pandas as pd
import phlo

from workflows.schemas.csv import EventsSchema


@phlo.ingestion(
    table_name="events",
    unique_key="event_id",
    validation_schema=EventsSchema,
    group="csv",
    freshness_hours=(1, 24),
)
def csv_events(partition_date: str) -> object:
    events = pd.read_csv(Path("data/events.csv"))
    events["event_id"] = events["id"].astype(str) + "-" + partition_date
    rows = events.to_dict(orient="records")
    return dlt.resource(rows, name="events")
```

This single function registers a partitioned ingestion asset, validates rows with Pandera, materializes through the configured orchestrator, lands the table in your configured storage and catalog, and becomes visible in Observatory and the catalog CLI; the starter wires the providers you install and generates the local runtime configuration.

## Quick Start

**Prerequisites**

- Python 3.11 or 3.12 for the currently tested support boundary; newer versions may resolve but are not CI-verified
- [`uv`](https://docs.astral.sh/uv/)
- Docker with Compose v2 (the currently supported path); Podman with a Compose provider is documented as experimental and is not CI-supported

```bash
# Create an isolated environment for the quickstart
mkdir phlo-quickstart && cd phlo-quickstart
uv venv
source .venv/bin/activate

# Install Phlo with the default local stack providers
uv pip install "phlo[defaults]"

# Create a project from the CSV batch starter
phlo init my-lakehouse --template csv-batch
cd my-lakehouse
uv pip install -e .

# Generate and start the local lakehouse stack
phlo services init
phlo services start

# Check that services are healthy
phlo services status
phlo doctor --verbose

# Materialize a completed daily partition
phlo materialize dlt_events --partition 2025-01-15

# Verify the table landed in the catalog
phlo catalog tables

# Stop the local stack when finished
phlo services stop
```

## Capabilities

- **Project layout** for `phlo.yaml`, workflows, schemas, transforms, tests, local runtime state, and project plugins.
- **Starters** for CSV ingestion, REST API ingestion, dbt medallion projects, Sling replication, and Observatory demos.
- **Python decorators** for registering ingestion, quality, and transformation assets without hand-writing provider boilerplate.
- **Local service commands** for generating, starting, checking, logging, and stopping the stack.
- **Provider packages** for Dagster, MinIO, Nessie, Trino, Iceberg, dbt, PostgreSQL, Observatory, and the rest of a working lakehouse.
- **Plugin hooks** for custom commands, services, assets, resources, catalogs, and Observatory extensions.

## How Phlo fits together

Phlo's core stays small. Installed provider packages contribute capabilities through Python entry points; the CLI discovers them in the current project and wires the runtime accordingly.

| Area | Intent | Provider examples |
| --- | --- | --- |
| Pipeline authoring | Define ingestion assets, schemas, checks, and transforms | `phlo-dlt`, `phlo-sling`, `phlo-airbyte`, `phlo-kafka`, `phlo-pandera`, `phlo-dbt` |
| Runtime services | Start the local lakehouse stack without hand-written Compose files | `phlo-dagster`, `phlo-postgres`, `phlo-minio`, `phlo-nessie`, `phlo-trino` |
| Table & catalog layer | Store, version, and query lakehouse tables | `phlo-iceberg`, `phlo-nessie`, `phlo-polaris`, `phlo-delta`, `phlo-clickhouse`, `phlo-openmetadata` |
| Product surfaces | Inspect and control assets, tables, lineage, quality, services, and logs | `phlo-api`, `phlo-observatory`, `phlo-mcp` |
| Serving & BI | Expose lakehouse data to apps and analysts | `phlo-hasura`, `phlo-postgrest`, `phlo-pgweb`, `phlo-superset` |
| Observability | Export telemetry, logs, metrics, and alerts | `phlo-otel`, `phlo-prometheus`, `phlo-loki`, `phlo-grafana`, `phlo-alerting` |
| Development | Test and validate projects and provider integrations | `phlo-testing` |

## Documentation

- [Installation Guide](docs/getting-started/installation.md)
- [Quickstart Guide](docs/getting-started/quickstart.md)
- [Core Concepts](docs/getting-started/core-concepts.md)
- [Choosing Components](docs/guides/choosing-components.md)
- [Workflow Development](docs/guides/workflow-development.md)
- [Plugin Development](docs/guides/plugin-development.md)
- [Operations Guide](docs/operations/operations-guide.md)
- [CLI Reference](docs/reference/cli-reference.md)

## Project status

Phlo is **alpha**. The local development workflow is usable and exercised in CI, but APIs, provider contracts, and the on-disk project layout may change before 1.0. Pin exact versions in production.

The machine-readable [v1 support boundary](registry/support/v1.json) defines the blessed target profile, preview and development-only packages, current runtime evidence, production prerequisites, exclusions, and release-gate status. A v1-target component is not a production-ready guarantee until those gates pass.

## Deployment support boundary

The v1 support boundary covers a single-project, single-tenant deployment. Kubernetes-native operation, high availability, and multi-region deployment are outside this support boundary.

## Development

```bash
uv pip install -e .
make check
```

Useful local service commands:

```bash
phlo services init
phlo services start
phlo services status
phlo services logs -f
phlo services stop
phlo doctor --verbose
```

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution terms. Run `make check` locally before opening a PR, and please open an issue first for larger changes so the design can be discussed up front.

## Licence

Phlo is open-source software licensed under the GNU Affero General Public
License, version 3 or later (AGPL-3.0-or-later). See [LICENSE](LICENSE).

If you wish to use Phlo in a proprietary product or service without complying
with the AGPL, please contact us about commercial licensing.
