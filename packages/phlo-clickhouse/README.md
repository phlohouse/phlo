# phlo-clickhouse

ClickHouse service and resource plugin for Phlo.

## Overview

`phlo-clickhouse` provides ClickHouse as a combined `table_store`, `query_engine`, and `publish_target` capability in Phlo. Unlike the existing bundled stack (DLT -> Iceberg -> Trino/dbt -> Postgres), ClickHouse can serve all three data plane roles in a single service.

## Installation

```bash
uv add phlo-clickhouse
```

## Usage

### Starting ClickHouse

```bash
phlo services start --service clickhouse
```

This starts both the ClickHouse server and the setup container that creates the default databases (`raw`, `staging`, `curated`, `marts`).

### Running Queries

```bash
phlo clickhouse query "SELECT version()"
phlo clickhouse query --file query.sql
```

### Checking Status

```bash
phlo clickhouse status
```

## Configuration

The following environment variables can be used to configure ClickHouse:

| Variable | Default | Description |
|----------|---------|-------------|
| `CLICKHOUSE_IMAGE` | `clickhouse/clickhouse-server:26.5.6.64-alpine@sha256:...` | Complete immutable ClickHouse image reference |
| `CLICKHOUSE_HTTP_PORT` | `8123` | ClickHouse HTTP interface port |
| `CLICKHOUSE_NATIVE_PORT` | `19000` | ClickHouse native protocol port |
| `CLICKHOUSE_METRICS_PORT` | `9363` | ClickHouse Prometheus metrics port |
| `CLICKHOUSE_USER` | `default` | ClickHouse default username |
| `CLICKHOUSE_PASSWORD` | | ClickHouse default user password |
| `CLICKHOUSE_DB` | `default` | Default ClickHouse database |

## Capabilities

This plugin registers the following capabilities:

- **Table Store**: ClickHouse MergeTree engine
- **Query Engine**: ClickHouse SQL
- **Publish Target**: ClickHouse marts database

## dbt Integration

Install with dbt support:

```bash
uv add "phlo-clickhouse[dbt]"
```

This provides the `dbt-clickhouse` adapter for running dbt transforms against ClickHouse.

## License

MIT
