# Phlo Packages Reference

Phlo is organized as a modular monorepo. Each package contributes a capability, runtime surface, or operator tool.

Use this section to answer:

- what does this package add to a Phlo stack?
- is it core, optional, or profile-specific?
- which other packages or capabilities does it connect to?

For environment-specific setup of external surfaces such as Hasura, PostgREST, or OpenMetadata, use [Setup](../setup/index.md).

Use the generated Python Reference in the pymdx site when you need symbol-level docstring and API reference for package modules.

## Current Versions

These versions come from the current package manifests on the checked-in release line. The v1 support boundary is tracked separately in [registry/support/v1.json](../../registry/support/v1.json); package version presence does not mean production readiness.

| Package | Version | Description |
| ------- | ------- | ----------- |
| `phlo` | `0.12.1` | Lakehouse platform |
| `phlo-alerting` | `0.4.0` | Alerting destinations and hooks for Phlo |
| `phlo-alloy` | `0.2.4` | alloy service plugin for Phlo |
| `phlo-api` | `0.7.0` | Phlo API - Backend service exposing Phlo internals to Observatory |
| `phlo-clickhouse` | `0.5.0` | ClickHouse service and resource plugin for Phlo |
| `phlo-clickstack` | `0.4.0` | clickstack service plugin for Phlo |
| `phlo-core-plugins` | `0.3.0` | Core plugin package for Phlo |
| `phlo-dagster` | `0.6.0` | Dagster service plugin for Phlo |
| `phlo-dbt` | `0.6.1` | dbt integration capability plugin for Phlo |
| `phlo-delta` | `0.5.0` | Delta Lake table-store capability plugin for Phlo |
| `phlo-airbyte` | `0.14.0` | Airbyte control-plane integration plugin for Phlo |
| `phlo-dlt` | `0.7.0` | DLT ingestion engine capability plugin for Phlo |
| `phlo-grafana` | `0.3.0` | grafana service plugin for Phlo |
| `phlo-hasura` | `0.3.0` | hasura service plugin for Phlo |
| `phlo-kafka` | `0.14.0` | Kafka ingestion service plugin for Phlo |
| `phlo-iceberg` | `0.6.0` | Iceberg table-store capability plugin for Phlo |
| `phlo-lineage` | `0.4.0` | Lineage tracking and visualization for Phlo |
| `phlo-loki` | `0.3.0` | loki service plugin for Phlo |
| `phlo-mcp` | `0.4.0` | MCP server for Phlo observability and lakehouse operations |
| `phlo-minio` | `0.4.0` | MinIO service plugin for Phlo |
| `phlo-nessie` | `0.5.0` | Nessie service plugin for Phlo |
| `phlo-oauth2-proxy` | `0.3.0` | oauth2-proxy service plugin for Phlo |
| `phlo-observatory` | `0.7.0` | Phlo Observatory - Data platform UI (bundled with phlo core) |
| `phlo-observatory-example` | `0.3.0` | Example Observatory extension plugin |
| `phlo-openmetadata` | `0.5.0` | OpenMetadata integration for Phlo |
| `phlo-polaris` | `0.14.0` | Apache Polaris catalog service plugin for Phlo |
| `phlo-otel` | `0.3.0` | OpenTelemetry traces and metrics for Phlo |
| `phlo-pandera` | `0.6.0` | Quality checks and schema utilities for Phlo |
| `phlo-pgweb` | `0.2.3` | pgweb service plugin for Phlo |
| `phlo-postgres` | `0.4.0` | Postgres service plugin for Phlo |
| `phlo-postgrest` | `0.2.6` | postgrest service plugin for Phlo |
| `phlo-prometheus` | `0.3.0` | prometheus service plugin for Phlo |
| `phlo-rustfs` | `0.4.0` | RustFS service plugin for Phlo |
| `phlo-sling` | `0.7.0` | Sling replication ingestion provider for Phlo |
| `phlo-superset` | `0.2.6` | superset service plugin for Phlo |
| `phlo-testing` | `0.4.0` | Testing utilities for Phlo |
| `phlo-traefik` | `0.3.0` | Traefik service plugin for Phlo |
| `phlo-trino` | `0.5.0` | Trino service plugin for Phlo |

## Historical Releases

Use the current version table for new installations. For older deployments, read
the docs from the matching git tag so examples, package versions, and migration
notes match the installed code.

| Release | Notes |
| ------- | ----- |
| `v0.12.1` | Current alpha release line. See the support boundary and changelog for the verified scope. |
| `v0.9.0` | Previous stable release before the May 2026 decorator APIs. |
| `v0.8.3` | Stable release after the 0.8.1 beta series and 0.8.2 publish recovery. |
| `v0.8.2` | Stable release that required manual tag recovery during publishing. |
| `v0.8.0` and earlier | Historical releases. Prefer upgrading before following current docs. |

## Complete Package Index

### Core Framework (1 package)

| Package | Description | Entry Points |
| ------- | ----------- | ------------ |
| **phlo** | Core framework with CLI, plugin system, configuration, and hooks | CLI, hooks, catalogs |

---

### Data Processing & Orchestration (9 packages)

| Package | Description | Category |
|---------|-------------|----------|
| [phlo-airbyte](phlo-airbyte.md) | Airbyte connector-managed ingestion control plane | Ingestion |
| [phlo-dagster](phlo-dagster.md) | Orchestration adapter for Dagster pipelines | Orchestration |
| [phlo-dbt](phlo-dbt.md) | dbt integration for SQL transformations | Transformation |
| [phlo-dlt](phlo-dlt.md) | Data Load Tool integration for data ingestion | Ingestion |
| [phlo-kafka](phlo-kafka.md) | Checkpoint-driven Kafka streaming ingestion | Ingestion |
| [phlo-sling](phlo-sling.md) | Sling-based data replication pipelines | Ingestion |
| [phlo-iceberg](phlo-iceberg.md) | Apache Iceberg table format and catalog | Storage |
| [phlo-delta](phlo-delta.md) | Delta Lake table format support | Storage |
| [phlo-pandera](phlo-pandera.md) | Data quality validation with Pandera | Quality |

---

### Query & Analytics (2 packages)

| Package | Description | Features |
|---------|-------------|----------|
| [phlo-trino](phlo-trino.md) | Distributed SQL query engine | Query federation, governance |
| [phlo-clickhouse](phlo-clickhouse.md) | High-performance analytical database | Fast aggregations |

---

### Data Lineage & Metadata (2 packages)

| Package | Description | Integration |
|---------|-------------|-------------|
| [phlo-lineage](phlo-lineage.md) | Data lineage tracking and graph storage | Dagster, dbt |
| [phlo-openmetadata](phlo-openmetadata.md) | Data catalog and governance platform | Nessie, Trino |

---

### Storage & Catalog (5 packages)

| Package | Description | Protocol |
|---------|-------------|----------|
| [phlo-nessie](phlo-nessie.md) | Git-like catalog for Iceberg tables | Nessie API |
| [phlo-polaris](phlo-polaris.md) | Apache Polaris Iceberg REST catalog with snapshot WAP | Iceberg REST |
| [phlo-postgres](phlo-postgres.md) | PostgreSQL metadata and state store | PostgreSQL |
| [phlo-minio](phlo-minio.md) | S3-compatible object storage | S3 API |
| [phlo-rustfs](phlo-rustfs.md) | High-performance S3-compatible storage | S3 API |

---

### Networking & Proxy (2 packages)

| Package | Description | Use Case |
|---------|-------------|----------|
| [phlo-traefik](phlo-traefik.md) | Reverse proxy with automatic service discovery | Local development URLs |
| [phlo-oauth2-proxy](phlo-oauth2-proxy.md) | OAuth2/OIDC forward-auth gateway | SSO with Traefik |

---

### Observability Stack (7 packages)

| Package | Description | Signal Type |
|---------|-------------|---------------|
| [phlo-clickstack](phlo-clickstack.md) | All-in-one observability (ClickHouse + Grafana) | All-in-one |
| [phlo-otel](phlo-otel.md) | OpenTelemetry trace/metric/log emission | Emission |
| [phlo-alloy](phlo-alloy.md) | OpenTelemetry collector and routing | Collection |
| [phlo-prometheus](phlo-prometheus.md) | Metrics collection and storage | Metrics |
| [phlo-grafana](phlo-grafana.md) | Metrics visualization dashboards | Visualization |
| [phlo-loki](phlo-loki.md) | Log aggregation and querying | Logs |
| [phlo-alerting](phlo-alerting.md) | Alert routing and notification management | Alerting |

---

### API Layer (4 packages)

| Package | Description | Protocol |
|---------|-------------|----------|
| [phlo-api](phlo-api.md) | REST API for Phlo internals | REST |
| [phlo-mcp](phlo-mcp.md) | MCP server for observability and lakehouse operations | MCP |
| [phlo-postgrest](phlo-postgrest.md) | Auto-generated REST API from PostgreSQL | REST |
| [phlo-hasura](phlo-hasura.md) | GraphQL API with real-time subscriptions | GraphQL |

---

### User Interface (3 packages)

| Package | Description | Purpose |
|---------|-------------|---------|
| [phlo-observatory](phlo-observatory.md) | Web UI for data exploration and monitoring | Primary UI |
| [phlo-superset](phlo-superset.md) | Business intelligence and visualization | BI/Reporting |
| [phlo-pgweb](phlo-pgweb.md) | PostgreSQL web administration interface | DB Admin |

---

### Development & Testing (2 packages)

| Package | Description | Use Case |
|---------|-------------|----------|
| [phlo-testing](phlo-testing.md) | Testing utilities, fixtures, and mocks | Development |
| [phlo-core-plugins](phlo-core-plugins.md) | Built-in quality checks and source connectors | Core functionality |

---

### Examples & Templates (1 package)

| Package | Description | Purpose |
|---------|-------------|---------|
| [phlo-observatory-example](phlo-observatory-example.md) | Example Observatory extension plugin | Reference implementation |

---

## Installation Profiles

### Full Installation (Recommended)

Install all default packages:

```bash
uv pip install phlo[defaults]
```

**Includes:** Core framework + dagster, postgres, trino, minio, nessie, iceberg, dlt, dbt, pandera

### Minimal Installation

Core framework only:

```bash
uv pip install phlo
```

### Root Extras

The root `phlo` package exposes a small set of extras. Other package families are installed by naming the packages directly.

| Extra | Use when | Included packages |
| --- | --- | --- |
| `phlo[defaults]` | You want the recommended local stack | `phlo-core-plugins`, `phlo-api`, `phlo-observatory`, `phlo-dagster`, `phlo-dlt`, `phlo-dbt`, `phlo-pandera`, `phlo-iceberg`, `phlo-postgres`, `phlo-minio`, `phlo-nessie`, `phlo-trino` |
| `phlo[core-services]` | You only want the core runtime services | `phlo-dagster`, `phlo-postgres`, `phlo-minio`, `phlo-nessie`, `phlo-trino` |
| `phlo[openmetadata]` | You want to add OpenMetadata to an existing install | `phlo-openmetadata` |

### Development Installation

For contributors and plugin developers:

```bash
# Clone the repository
git clone https://github.com/phlohouse/phlo.git
cd phlo

# Install with all dev dependencies
uv pip install -e ".[dev]"

# Or install specific packages in editable mode
uv pip install -e ./packages/phlo-dlt -e ./packages/phlo-pandera
```

---

## Plugin Entry Points

Packages register plugins through Python entry points. These are the entry point groups currently used by the workspace package metadata:

| Entry Point Group | Description | Example Provider |
|-------------------|-------------|------------------|
| `phlo.plugins.services` | Infrastructure service definitions | phlo-postgres, phlo-minio |
| `phlo.plugins.sources` | Data source connectors | phlo-core-plugins |
| `phlo.plugins.ingestion_providers` | Ingestion system providers | phlo-dlt, phlo-sling |
| `phlo.plugins.quality` | Quality check implementations | phlo-core-plugins |
| `phlo.plugins.quality_providers` | Quality validation providers | phlo-pandera |
| `phlo.plugins.transformation_providers` | Transformation providers | phlo-dbt |
| `phlo.plugins.transforms` | Data transformation tools | phlo |
| `phlo.plugins.catalogs` | Catalog configurations | phlo-nessie, phlo-openmetadata |
| `phlo.plugins.orchestrators` | Orchestrator adapters | phlo-dagster |
| `phlo.plugins.assets` | Asset definition providers | phlo-dlt, phlo-dbt, phlo-sling |
| `phlo.plugins.resources` | Resource and runtime capability providers | phlo-iceberg, phlo-trino, phlo-postgres |
| `phlo.plugins.cli` | CLI command extensions | phlo-nessie, phlo-dbt, phlo-sling |
| `phlo.plugins.hooks` | Event hook handlers | phlo-otel, phlo-alerting |
| `phlo.plugins.observatory` | Observatory UI extensions | phlo-observatory-example |

### Discovering Plugins

```bash
# List all installed plugins
phlo plugin list

# List by type
phlo plugin list --type services
phlo plugin list --type sources
phlo plugin list --type quality

# Get detailed plugin information
phlo plugin info dagster
phlo plugin info pandera
```

---

## Common Package Combinations

### Basic Lakehouse (Minimal)

```bash
uv pip install phlo phlo-dagster phlo-postgres phlo-trino phlo-minio phlo-nessie
```

### Full Pipeline Stack

```bash
uv pip install phlo phlo-dagster phlo-postgres phlo-trino phlo-minio \
  phlo-nessie phlo-iceberg phlo-dlt phlo-dbt phlo-pandera
```

### With Observability

```bash
uv pip install phlo phlo-dagster phlo-postgres phlo-trino phlo-minio \
  phlo-nessie phlo-iceberg phlo-dlt phlo-dbt phlo-pandera \
  phlo-otel phlo-clickstack phlo-alloy phlo-prometheus phlo-grafana \
  phlo-loki phlo-alerting
```

### With API Layer

```bash
uv pip install phlo phlo-dagster phlo-postgres phlo-trino phlo-minio \
  phlo-nessie phlo-iceberg phlo-dlt phlo-dbt phlo-pandera \
  phlo-api phlo-postgrest phlo-hasura
```

### Complete Stack

```bash
uv pip install "phlo[defaults]" \
  phlo-alerting phlo-alloy phlo-clickhouse phlo-clickstack phlo-delta \
  phlo-grafana phlo-hasura phlo-lineage phlo-loki phlo-mcp \
  phlo-openmetadata phlo-otel phlo-pgweb phlo-postgrest \
  phlo-prometheus phlo-rustfs phlo-sling phlo-superset phlo-traefik
```

---

## Package Versioning

Phlo packages are versioned independently. Install compatible packages from the
same current release set instead of forcing every package to the same version.
The table at the top of this page is the authoritative set for the current
release.

```bash
# Check installed versions
pip list | grep phlo

# Example output for the current release set:
# phlo              0.12.1
# phlo-dagster      0.6.0
# phlo-dbt          0.6.1
# phlo-dlt          0.7.0
```

---

## Creating Custom Packages

Build your own packages that integrate with Phlo:

1. See [Plugin Development Guide](../guides/plugin-development.md) for API details
2. Use [phlo-observatory-example](phlo-observatory-example.md) as a reference
3. Register through entry points for automatic discovery

---

## Next Steps

- [Getting Started](../getting-started/quickstart.md) - Run your first pipeline
- [Plugin Development Guide](../guides/plugin-development.md) - Build custom integrations
- [CLI Reference](../reference/cli-reference.md) - Manage packages and services
- [Architecture Overview](../reference/architecture.md) - Understand the system design
