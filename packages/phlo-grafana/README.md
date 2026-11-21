# phlo-grafana

Grafana visualization service for Phlo.

## Description

Metrics visualization and dashboards for observability. Pre-configured with datasources for Prometheus, Loki, and PostgreSQL. Grafana's Trino data source is a separate plugin download, so it is not provisioned.

## Installation

```bash
phlo services add grafana
# or, for the whole observability stack
phlo services add --profile observability
```

## Profile

Part of the `observability` profile.

## Configuration

| Variable                 | Default  | Description         |
| ------------------------ | -------- | ------------------- |
| `GRAFANA_PORT`           | `3003`   | Grafana web UI port |
| `GRAFANA_VERSION`        | `13.1.1` | Grafana version     |
| `GRAFANA_ADMIN_USER`     | `admin`  | Admin username      |
| `GRAFANA_ADMIN_PASSWORD` | generated | Admin password, in `.phlo/secrets/.env` |

## Auto-Configuration

This package is **fully auto-configured**:

| Feature            | How It Works                                         |
| ------------------ | ---------------------------------------------------- |
| **Datasources**    | Pre-provisioned: Prometheus, Loki, PostgreSQL        |
| **Dashboards**     | Pre-provisioned dashboards in `grafana/dashboards/`  |
| **Metrics Labels** | Exposes Grafana metrics for Prometheus               |

### Pre-Configured Datasources

| Datasource | Type       | URL                    |
| ---------- | ---------- | ---------------------- |
| Prometheus | prometheus | http://prometheus:9090 |
| Loki       | loki       | http://loki:3100       |
| PostgreSQL | postgres   | postgres:5432          |

The PostgreSQL data source signs in with the project's generated Postgres credentials.

## Usage

```bash
# Start with observability profile
phlo services start --profile observability

# Or start individually
phlo services start --service grafana
```

## Endpoints

- **Web UI**: `http://localhost:3003`
- **Login**: admin / admin

## Entry Points

- `phlo.plugins.services` - Provides `GrafanaServicePlugin`
