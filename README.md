<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/phlohouse/phlo/main/.github/readme-header-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/phlohouse/phlo/main/.github/readme-header.png">
  <img src="https://raw.githubusercontent.com/phlohouse/phlo/main/.github/readme-header.png" alt="Phlo" width="900">
</picture>

# Phlo

[![PyPI](https://img.shields.io/pypi/v/phlo.svg)](https://pypi.org/project/phlo/)
[![Python](https://img.shields.io/pypi/pyversions/phlo.svg)](https://pypi.org/project/phlo/)
[![CI](https://github.com/phlohouse/phlo/actions/workflows/ci.yml/badge.svg)](https://github.com/phlohouse/phlo/actions/workflows/ci.yml)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#status)

**A plug-and-play lakehouse framework for Python.** Phlo's core is small. Orchestration, ingestion, storage, catalog, query, quality, transformation, serving and observability each come from a `phlo-*` package that you install, swap or leave out. Install a package and its CLI commands, services and Observatory panels appear. You don't wire anything by hand.

Your pipelines are decorated Python functions that ask for a capability, such as a table store or a catalog, rather than a specific product. The default stack is Dagster, dlt, dbt, Pandera, Iceberg, Nessie, Trino, MinIO and Postgres. Switch Iceberg for Delta, Nessie for Polaris or dbt for phlo-transform, or add Sling, Kafka, ClickHouse, Hasura, Superset or Grafana. `phlo services init` regenerates the stack from whatever is installed.

> Phlo is **alpha** and not production-ready. See [Status](#status).

## What it looks like

```python
import dlt
import pandas as pd
import pandera.pandas as pa
import phlo


class EventsSchema(pa.DataFrameModel):
    id: int
    name: str
    value: int = pa.Field(gt=0)


@phlo.ingest.dlt(
    table_name="events",          # lands as raw.events; asset key dlt_events
    unique_key="id",              # merge key for re-runs
    validation_schema=EventsSchema,
    group="csv",
    freshness_hours=(1, 24),      # warn after 1h, error after 24h
)
def csv_events(partition_date: str) -> object:
    rows = pd.read_csv("data/events.csv").to_dict(orient="records")
    return dlt.resource(rows, name="events")
```

That one function becomes a daily-partitioned Dagster asset. On each run Phlo extracts the rows with dlt, stages them, validates them against `EventsSchema`, and only then merges them on `unique_key` into an Iceberg table registered in Nessie. Validation is strict by default: one failing row fails the run and the table is left as it was. The function never names Dagster, Iceberg or Nessie, so changing a provider doesn't change this code.

## Quick start

You need Python 3.12 or 3.13, [`uv`](https://docs.astral.sh/uv/), and Docker with Compose v2. On Windows, use WSL 2.

```bash
uv venv && source .venv/bin/activate
uv pip install "phlo[defaults]"

phlo init my-lakehouse --template csv-batch
cd my-lakehouse
uv sync

uv run phlo services init               # render the stack into .phlo/
uv run phlo services start
uv run phlo doctor                      # every check should pass

uv run phlo materialize dlt_events --partition 2025-01-15
uv run phlo catalog tables              # raw.events is there
uv run phlo trino --catalog iceberg     # query it

uv run phlo services stop
```

While the stack is running, Observatory is at <http://localhost:3001> and Dagster at <http://localhost:10006>. `phlo services ports` lists the rest.

`phlo services init` generates a random password for every service into `.phlo/.env.local`. There are no default credentials.

The [first pipeline tutorial](docs/getting-started/first-pipeline.md) walks through the same steps, including breaking the schema on purpose to watch a run get blocked. `phlo init --list-templates` shows the other starters: `minimal` (the default), `api-ingestion`, `basic` and `dbt-medallion` (dbt), `sling-replication`, and `observability-demo`.

## What you get

- **One decorator family per stage.** `phlo.ingest`, `phlo.quality`, `phlo.publish` and `phlo.export`, with behaviour set by arguments (merge strategy, schema policy, strictness, freshness) rather than by separate decorators.
- **Write-audit-publish.** Set `wap.enabled: true` in `phlo.yaml` and every `phlo materialize` and `phlo backfill` runs on its own Nessie branch, which is merged only after its checks pass. `phlo branch` lets you list, diff and merge branches yourself.
- **Generated runtime.** `phlo services` renders, starts, upgrades and inspects the Compose stack from the packages you have installed. You don't hand-write Compose files.
- **Schema change control.** `phlo schema-migrate` plans and applies table changes against your Pandera schemas; `phlo contracts` checks compatibility between snapshots.
- **Operations as planned commands.** `phlo operations backup`, `restore`, `maintenance` and `upgrade` show a plan before they change anything.
- **Run evidence and audit.** Each run stores versioned evidence of its stages, lineage, quality results and catalog changes. `phlo audit` queries it and `phlo compliance export-evidence` packages it.
- **Observatory.** A web UI for assets, tables, lineage, quality results, services and logs.

## Swap any piece

Each part of the stack is a named capability. Phlo resolves it to an installed provider, set project-wide or per asset.

| Capability | Default | Alternatives |
| --- | --- | --- |
| Orchestration | `phlo-dagster` | |
| Ingestion | `phlo-dlt` | `phlo-sling`, `phlo-airbyte`, `phlo-kafka` |
| Quality | `phlo-pandera` | |
| Transformation | `phlo-dbt` | `phlo-transform` |
| Table format | `phlo-iceberg` | `phlo-delta` |
| Catalog | `phlo-nessie` | `phlo-polaris`, `phlo-openmetadata` (metadata sync) |
| Object storage | `phlo-minio` | `phlo-rustfs` |
| Query | `phlo-trino` | `phlo-clickhouse` |
| Serving and BI | | `phlo-postgrest`, `phlo-hasura`, `phlo-superset`, `phlo-pgweb` |
| Observability | `phlo-observatory`, `phlo-observe-plugin` | `phlo-otel`, `phlo-prometheus`, `phlo-loki`, `phlo-grafana`, `phlo-alloy`, `phlo-clickstack`, `phlo-alerting` |
| Access and ingress | | `phlo-oauth2-proxy`, `phlo-traefik` |
| Agents | | `phlo-mcp` |

```bash
uv add phlo-polaris                     # Iceberg tables on a Polaris catalog instead of Nessie
export PHLO_DEFAULT_CAPABILITIES='{"catalog":"polaris"}'
uv run phlo services init && uv run phlo services start
```

You can also override one asset with `capabilities={"table_store": "iceberg"}` on its decorator. [Choose your stack](docs/guides/choose-your-stack.md) covers the details, and the [polaris-streaming](examples/lakehouses/polaris-streaming) and [delta-portability](examples/lakehouses/delta-portability) examples show non-default stacks end to end. Your own code plugs in the same way. A plugin can add a provider, a CLI command, a service or an Observatory panel through Python entry points, and `phlo plugin create` scaffolds one.

### Support levels

The packages don't all carry the same promise. `phlo[defaults]` installs the core.

| Support level | Packages |
| --- | --- |
| **Core** (`phlo[defaults]`, the v1 target) | `phlo-dagster`, `phlo-dlt`, `phlo-dbt`, `phlo-pandera`, `phlo-iceberg`, `phlo-nessie`, `phlo-trino`, `phlo-minio`, `phlo-postgres`, `phlo-api`, `phlo-observatory`, `phlo-observe-plugin`, `phlo-core-plugins` |
| **Supported optional** | `phlo-sling`, `phlo-hasura`, `phlo-postgrest`, `phlo-lineage`, `phlo-alerting`, `phlo-otel`, `phlo-oauth2-proxy`, `phlo-traefik` |
| **Preview** (works, no v1 guarantee) | `phlo-airbyte`, `phlo-kafka`, `phlo-delta`, `phlo-polaris`, `phlo-clickhouse`, `phlo-clickstack`, `phlo-rustfs`, `phlo-openmetadata`, `phlo-superset`, `phlo-grafana`, `phlo-loki`, `phlo-prometheus`, `phlo-alloy`, `phlo-mcp` |
| **Development only** | `phlo-testing`, `phlo-transform`, `phlo-pgweb`, `phlo-observatory-example` |

The [support matrix](docs/reference/support-matrix.md) and the machine-readable [support manifest](registry/support/v1.json) are the source of truth. `phlo support status` compares what you have installed against it.

## Status

Phlo is **alpha** and is not production-ready. APIs, provider contracts and the project layout can still change. It has one maintainer.

The path to 1.0 is set out in [ADR 0053](docs/architecture/decisions/0053-what-1-0-promises.md):

- **0.19.0** is the last alpha and completes the scheduled removals.
- **0.20.0** removes the legacy `/api/observatory` routes and starts the 1.0 candidate line.
- **1.0.0** ships only when the five release gates (security, run evidence, maintenance, upgrade and restore, golden path) pass on a live deployment, and the candidate has run that deployment for seven days without intervention.

Scope for 1.0 is one project and one tenant on Docker Compose, on Linux (macOS for local use, Windows via WSL 2). Kubernetes, high availability and multi-region are out of scope.

Phlo is not validated for GxP or other regulated use. Regulated mode and evidence packs exist, but no part of Phlo has been qualified against a regulatory standard.

## Documentation

- [Install](docs/getting-started/install.md) and [build your first pipeline](docs/getting-started/first-pipeline.md)
- [How Phlo works](docs/concepts/how-phlo-works.md) and [write-audit-publish](docs/concepts/write-audit-publish.md)
- Guides: [ingest data](docs/guides/ingest-data.md), [add quality checks](docs/guides/add-quality-checks.md), [transform with dbt](docs/guides/transform-with-dbt.md), [choose your stack](docs/guides/choose-your-stack.md), [secure the stack](docs/guides/secure-the-stack.md), [back up and recover](docs/guides/maintain-and-recover.md), [upgrade Phlo](docs/guides/upgrade-phlo.md)
- [Example lakehouses](examples/lakehouses) covering seventeen source and architecture patterns
- Reference: [CLI](docs/reference/cli.md), [Python API](docs/reference/python-api.md), [configuration](docs/reference/configuration.md), [errors](docs/reference/errors.md)

## Related projects

- [phlo-transform](https://github.com/phlohouse/phlo-transform): a Rust SQL build engine with structural lineage and planning. It is not dbt-compatible, and it can translate a dbt project.
- [phlo-observe](https://github.com/phlohouse/phlo-observe): structured, wide-event observability for Phlo runs.
- [stencil](https://github.com/phlohouse/stencil): map cells in an Excel template once, then extract typed data from every copy of it.

## Development

You need Python 3.12, `uv`, and Node.js 24 or later.

```bash
make setup    # Python env plus Observatory dependencies
make check    # lint, types, tests, docs links, support manifest
```

Issues and pull requests are welcome. Open an issue before a large change. [CONTRIBUTING.md](CONTRIBUTING.md) has the workflow and the contribution terms.

## Licence

AGPL-3.0-or-later. See [LICENSE](LICENSE). If you want to use Phlo in a proprietary product or service without complying with the AGPL, contact us about commercial licensing.
