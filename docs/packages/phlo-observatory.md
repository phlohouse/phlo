# phlo-observatory

Phlo Observatory UI for data platform visibility.

## Overview

`phlo-observatory` is a web-based UI for exploring the data lakehouse. It enables viewing lineage, browsing tables, running queries, and monitoring pipeline health.

## Installation

```bash
pip install phlo-observatory
# or
phlo plugin install observatory
```

## Configuration

Observatory configuration is primarily managed through the service definition (`service.yaml`). The following environment variables are used by the Observatory container:

| Variable              | Default                       | Description              |
| --------------------- | ----------------------------- | ------------------------ |
| `OBSERVATORY_PORT`    | `3001`                        | Observatory web UI port (container) |
| `DAGSTER_GRAPHQL_URL` | `http://dagster:3000/graphql` | Dagster GraphQL endpoint |
| `NESSIE_URL`          | `http://nessie:19120/api/v2`  | Nessie API URL           |
| `TRINO_URL`           | `http://trino:8080`           | Trino HTTP URL           |
| `PHLO_API_URL`        | `http://phlo-api:4000`        | Phlo API URL             |

For persistent settings storage:

| Variable                        | Default | Description                                |
| ------------------------------- | ------- | ------------------------------------------ |
| `PHLO_OBSERVATORY_SETTINGS_DB_URL` | -    | PostgreSQL DSN for settings storage        |

## Features

### Core Capabilities

| Feature               | Description                                    |
| --------------------- | ---------------------------------------------- |
| **Data Explorer**     | Browse tables, view schemas, preview data      |
| **Lineage Graph**     | Interactive visualization of data flow         |
| **Asset Browser**     | View Dagster assets and materialization status |
| **Quality Dashboard** | Monitor quality check results                  |
| **Branch Management** | Create, view, and merge Nessie branches        |
| **SQL Workbench**     | Run bounded read-only previews against known Trino tables |

### Auto-Configuration

| Feature            | How It Works                               |
| ------------------ | ------------------------------------------ |
| **API Connection** | Connects to phlo-api for backend data      |
| **Service URLs**   | Auto-configured from environment variables |
| **Local source mode** | Hot-reloading after `services init --dev` |

## Usage

### Starting the Service

```bash
# Start Observatory
phlo services start --service observatory

# Start with native mode (better for ARM Macs)
phlo services start --native

# For local source development, initialize the project in dev mode first.
phlo services init --dev --phlo-source /path/to/phlo
phlo services start --service observatory
```

### Accessing the UI

Open `http://localhost:3001` in your browser.

## UI Sections

### Data Explorer

Browse and explore your data lakehouse:

- View all schemas and tables
- Inspect table schemas and statistics
- Preview data with pagination
- Export query results

### Lineage Graph

Visualize data dependencies:

- Interactive node-based graph
- Click tables to see details
- Filter by upstream/downstream
- Highlight specific paths

### Asset Browser

Monitor Dagster assets:

- View materialization status
- See last run timestamps
- Check freshness policies
- Inspect materialization status; materialization and backfill actions remain preview-only and guarded

### Quality Dashboard

Track data quality:

- View check results over time
- Filter by status (pass/fail)
- Drill into failure details
- See violation samples

### Branch Manager

Work with Nessie branches:

- List all branches
- Create new branches
- Compare branches
- Merge branches

### SQL Workbench

Run ad-hoc queries:

- Syntax highlighting
- Auto-complete for tables
- Result pagination
- Export to CSV

## Endpoints

| Endpoint   | URL                     |
| ---------- | ----------------------- |
| **Web UI** | `http://localhost:3001` |

## Entry Points

| Entry Point             | Plugin                     |
| ----------------------- | -------------------------- |
| `phlo.plugins.services` | `ObservatoryServicePlugin` |

## Related Packages

- [phlo-api](phlo-api.md) - Backend API
- [phlo-lineage](phlo-lineage.md) - Lineage data
- [phlo-pandera](phlo-pandera.md) - Quality checks
- [phlo-nessie](phlo-nessie.md) - Branch management

## Regulated Mode

Observatory is inside the regulated boundary because all data and mutation
operations go through `phlo-api`. No additional authorization adapter is
needed in Observatory itself.

In regulated deployments, access Observatory only through Traefik with
proxy authentication enabled. Direct port access bypasses ingress auth.

For details on regulatory boundary status and ingress authentication assumptions,
see [Browser-Facing Surfaces in Regulated Mode](../setup/security.md#browser-facing-surfaces-in-regulated-mode).

## Durable run-report support boundary

The Observatory exposes an authenticated durable per-run report API and UI projection at alpha maturity. The v1 support boundary acknowledges this implemented capability while keeping release acceptance pending until the run_evidence gate passes.

## Next Steps

- [Installation Guide](../getting-started/installation.md) - Complete setup
- [Quickstart](../getting-started/quickstart.md) - First steps
- [Troubleshooting](../operations/troubleshooting.md) - Debug issues
