# phlo-observatory

Phlo Observatory UI for data platform visibility.

## Description

Web-based UI for exploring the data lakehouse through Phlo API. View lineage, browse assets, run bounded read-only queries, and monitor pipeline health. The durable per-run report remains available through Phlo API; its dedicated UI projection is pending.

## Installation

```bash
pip install phlo-observatory
# or
phlo plugin install observatory
```

## Configuration

| Variable           | Default                | Description             |
| ------------------ | ---------------------- | ----------------------- |
| `OBSERVATORY_PORT` | `3001`                 | Observatory web UI port |
| `PHLO_API_URL`     | `http://phlo-api:4000` | Server-side Phlo API URL |

The API-backed frontend is the only UI in the package and container image.
The old frontend and selector have been removed. Configure the authenticated
proxy and Phlo API before exposing the service. The server forwards the user's
token to Phlo API, which verifies it independently. Rollback requires deploying
the previous image. Legacy backend API routes and Python extension contracts
remain; browser-extension loading is not implemented in this UI.
See [setup and verification](src/phlo_observatory/README.md) for authentication
requirements and frontend commands. `/healthz` checks frontend liveness only.

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
