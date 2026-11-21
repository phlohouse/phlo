# phlo-postgres

PostgreSQL database service for Phlo.

## Description

Core PostgreSQL database for metadata storage, lineage tracking, and operational data. Includes optional Prometheus exporter for database metrics.

## Installation

```bash
uv add phlo-postgres
# or
phlo plugin install postgres
```

## Configuration

| Variable                 | Default  | Description            |
| ------------------------ | -------- | ---------------------- |
| `POSTGRES_PORT`          | `10000`  | PostgreSQL host port   |
| `POSTGRES_USER`          | `phlo`   | Database username      |
| `POSTGRES_PASSWORD`      | `phlo`   | Database password      |
| `POSTGRES_DB`            | `phlo`   | Database name          |
| `POSTGRES_SSL_MODE`      | `prefer` | SSL mode               |
| `POSTGRES_EXPORTER_PORT` | `9187`   | Postgres exporter port |

## Auto-Configuration

This package is **fully auto-configured**:

| Feature                | How It Works                                               |
| ---------------------- | ---------------------------------------------------------- |
| **Grafana Datasource** | Auto-registers as Grafana datasource via labels            |
| **postgres-exporter**  | Optional Prometheus exporter for native PostgreSQL metrics |
| **Service Discovery**  | Exporter auto-scraped by Prometheus                        |

### Grafana Labels

```yaml
compose:
  labels:
    phlo.grafana.datasource: "true"
    phlo.grafana.datasource.type: "postgres"
    phlo.grafana.datasource.name: "PostgreSQL"
```

## Usage

```bash
# Start PostgreSQL
phlo services start --service postgres

# Start with exporter (for observability)
phlo services start --service postgres,postgres-exporter
```

## Endpoints

- **PostgreSQL**: `localhost:10000`
- **Exporter Metrics**: `http://localhost:9187/metrics`

## Entry Points

- `phlo.plugins.services` - Provides `PostgresServicePlugin` and `PostgresExporterServicePlugin`
