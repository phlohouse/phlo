# phlo-trino

Trino distributed SQL query engine for Phlo.

## Overview

`phlo-trino` provides the Trino query engine for SQL access to the Iceberg lakehouse. It enables fast analytics queries across all data layers.

## Installation

```bash
pip install phlo-trino
# or
phlo plugin install trino
```

## Configuration

| Variable        | Default    | Description     |
| --------------- | ---------- | --------------- |
| `TRINO_PORT`    | `10005`    | Trino HTTP host port in generated projects |
| `TRINO_VERSION` | `477`      | Trino version   |
| `TRINO_HOST`    | `trino`    | Trino hostname  |
| `TRINO_CATALOG` | `iceberg`  | Default catalog |

## Features

### Auto-Configuration

| Feature                 | How It Works                                                                 |
| ----------------------- | ---------------------------------------------------------------------------- |
| **Catalog Discovery**   | Auto-generates catalog files from installed `phlo.plugins.catalogs` entry points |
| **Metrics Labels**      | Exposes Trino metrics for Prometheus                                         |
| **Grafana Datasource**  | Auto-registers as Grafana datasource via labels                              |
| **Superset Connection** | Auto-registered in Superset via Superset hook                                |

### Catalog Generation

Trino catalog `.properties` files are generated from installed catalog plugins:

```python
from phlo_trino.catalog_generator import generate_catalog_files

# Discovers CatalogPlugin instances targeting "trino" and generates files
generate_catalog_files("/path/to/trino/catalog/")
```

Catalog plugins declare `targets` in code; Trino loads only those that target `trino`.

### Default Catalogs

| Catalog         | Description                                                |
| --------------- | ---------------------------------------------------------- |
| `iceberg`       | Main Iceberg catalog (main branch)                       |
| `iceberg_{ref}` | Branch-specific catalogs (e.g., `iceberg_dev`, `iceberg_feature_x`) |
| `postgres`      | PostgreSQL connection for marts                            |

## Usage

### Starting the Service

```bash
# Start Trino
phlo services start --service trino

# Start with all dependencies
phlo services start
```

### CLI Queries

```bash
# Run SQL query
phlo trino query "SELECT * FROM iceberg.bronze.users LIMIT 10"

# Interactive shell
phlo trino

# Start in a specific catalog/schema
phlo trino --catalog iceberg --schema raw
```

### SQL Examples

```sql
-- Query bronze layer
SELECT * FROM iceberg.bronze.users LIMIT 10;

-- Query from dev branch
SELECT * FROM iceberg_dev.bronze.users LIMIT 10;

-- Time travel query
SELECT * FROM iceberg.bronze.users FOR VERSION AS OF 123456789;

-- Cross-schema join
SELECT
    u.id,
    u.name,
    e.event_count
FROM iceberg.silver.users u
JOIN iceberg.gold.user_events e ON u.id = e.user_id;

-- Query PostgreSQL marts
SELECT * FROM postgres.public.mrt_daily_summary;
```

### Programmatic Access

```python
from trino.dbapi import connect

conn = connect(
    host="localhost",
    port=8080,
    user="phlo",
    catalog="iceberg",
    schema="bronze"
)

cursor = conn.cursor()
cursor.execute("SELECT * FROM users LIMIT 10")
results = cursor.fetchall()
```

## Endpoints

| Endpoint     | URL                             |
| ------------ | ------------------------------- |
| **HTTP API** | `http://localhost:10005`         |
| **Web UI**   | `http://localhost:10005/ui`      |
| **Metrics**  | `http://localhost:10005/v1/info` |

## Grafana Integration

Trino is automatically registered as a Grafana datasource:

```yaml
compose:
  labels:
    phlo.grafana.datasource: "true"
    phlo.grafana.datasource.type: "trino"
    phlo.grafana.datasource.name: "Trino"
```

## Audit Logging

`phlo-trino` provides query history and coordinator logs, which form the starting point for query and access trails. Long-term retention and export are still operator-managed, so do not rely on the local Trino container alone for production audit evidence.

## Entry Points

| Entry Point                     | Plugin                                               |
| ------------------------------- | ---------------------------------------------------- |
| `phlo.plugins.services`         | `TrinoServicePlugin`                                 |
| `phlo.plugins.resource_providers` | `TrinoResourceProvider`                              |
| `phlo.plugins.catalogs`       | Discovers external catalog adapters targeting Trino |

## Related Packages

- [phlo-iceberg](phlo-iceberg.md) - Table format
- [phlo-nessie](phlo-nessie.md) - Catalog service
- [phlo-postgres](phlo-postgres.md) - Marts storage
- [phlo-grafana](phlo-grafana.md) - Visualization
- [Audit Logging](../operations/audit-logging.md) - Platform audit-log posture

## Next Steps

- [DuckDB Queries](../reference/duckdb-queries.md) - Ad-hoc analysis
- [API Reference](../reference/phlo-api.md) - REST/GraphQL access
- [dbt Development](../guides/dbt-development.md) - SQL transformations

Delta Lake preview catalog: `catalog/delta.properties` ships alongside the iceberg catalogs (file metastore rooted at the Delta warehouse path).
