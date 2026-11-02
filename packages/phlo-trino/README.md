# phlo-trino

Trino distributed SQL query engine for Phlo.

## Description

Trino query engine for querying the Iceberg lakehouse. Provides SQL access to all lakehouse tables.

## Installation

```bash
pip install phlo-trino
# or
phlo plugin install trino
```

## Configuration

| Variable        | Default   | Description     |
| --------------- | --------- | --------------- |
| `TRINO_PORT`    | `10005`   | Trino HTTP host port |
| `TRINO_VERSION` | `467`     | Trino version   |
| `TRINO_HOST`    | `trino`   | Trino hostname  |
| `TRINO_CATALOG` | `iceberg` | Default catalog |

The generated container starts with a short root bootstrap, then runs Trino as
`trino:trino`. The bootstrap grants that group read access to the six bundled
configuration files and traversal of their parent directory. This supports host
`umask 077` without making private configuration world-readable. Host file owners
remain unchanged so configuration can be regenerated. Trino cannot write to the
parent directory or replace the root-executed bootstrap.

Before changing permissions, the bootstrap rejects symlinks and nonregular files
at all six configuration paths. This prevents a substituted catalog file from
redirecting a privileged permission change during restart.

The dynamic catalog directory remains writable by Trino and retains its host
group. Other catalog files and the bootstrap file keep their existing permissions.
Custom files, including certificates and additional catalogs, require explicit
runtime access permissions; the bootstrap does not change them recursively.

## Auto-Configuration

This package is **fully auto-configured**:

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

### Grafana Labels

```yaml
compose:
  labels:
    phlo.grafana.datasource: "true"
    phlo.grafana.datasource.type: "trino"
    phlo.grafana.datasource.name: "Trino"
```

## Usage

```bash
# Start Trino
phlo services start --service trino

# Query via CLI
phlo trino query "SELECT * FROM iceberg.bronze.users LIMIT 10"

# Interactive shell
phlo trino

# Interactive shell with catalog/schema selected
phlo trino --catalog iceberg --schema raw
```

## Endpoints

- **HTTP API**: `http://localhost:10005`
- **Trino UI**: `http://localhost:10005/ui`

## Entry Points

- `phlo.plugins.services` - Provides `TrinoServicePlugin`
- `phlo.plugins.resources` - Provides `TrinoResourceProvider`
