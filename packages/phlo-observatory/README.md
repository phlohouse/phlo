# phlo-observatory

Phlo Observatory UI for data platform visibility.

## Description

Web-based UI for exploring the data lakehouse. View lineage, browse tables, run bounded read-only previews, and monitor pipeline health; an authenticated durable per-run report API and UI projection are implemented at alpha maturity.

## Installation

```bash
pip install phlo-observatory
# or
phlo plugin install observatory
```

## Configuration

| Variable              | Default                       | Description              |
| --------------------- | ----------------------------- | ------------------------ |
| `OBSERVATORY_PORT`    | `3001`                        | Observatory web UI port  |
| `DAGSTER_GRAPHQL_URL` | `http://dagster:3000/graphql` | Dagster GraphQL endpoint |
| `NESSIE_URL`          | `http://nessie:19120/api/v2`  | Nessie API URL           |
| `TRINO_URL`           | `http://trino:8080`           | Trino HTTP URL           |
| `PHLO_API_URL`        | `http://phlo-api:4000`        | Phlo API URL             |

## Auto-Configuration

This package is **auto-configured** via environment:

| Feature            | How It Works                               |
| ------------------ | ------------------------------------------ |
| **API Connection** | Connects to phlo-api for backend data      |
| **Service URLs**   | Auto-configured from environment variables |
| **Local source mode** | Hot-reloading after `services init --dev` |

## Usage

```bash
# Start Observatory
phlo services start --service observatory

# For local source development, initialize the project in dev mode first.
phlo services init --dev --phlo-source /path/to/phlo
phlo services start --service observatory
```

## Features

- **Data Explorer** - Browse tables, view schemas, and preview known tables with bounded read-only queries
- **Lineage Graph** - Visualize data flow and dependencies
- **Asset Browser** - View Dagster assets and materialization status
- **Quality Dashboard** - Monitor quality check results
- **Branch Management** - Create and merge Nessie branches

## Endpoints

- **Web UI**: `http://localhost:3001`

## Entry Points

- `phlo.plugins.services` - Provides `ObservatoryServicePlugin`
