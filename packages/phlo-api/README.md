# phlo-api

Backend API service for Phlo Observatory.

## Description

FastAPI-based backend service exposing Phlo internals to the Observatory UI. Provides endpoints for lineage, quality checks, assets, and metadata.

## Installation

```bash
pip install phlo-api
# or
phlo plugin install api
```

## Configuration

| Variable        | Default   | Description     |
| --------------- | --------- | --------------- |
| `PHLO_API_PORT` | `4000`    | API server port |
| `HOST`          | `0.0.0.0` | API server host |
| `PHLO_AUTHORIZATION_BACKEND` | unset | Authorization backend capability name |
| `PHLO_AUTHORIZATION_MODE` | `required` in production, `optional` otherwise | Guard behavior when no authorization backend exists |

`PHLO_AUTHORIZATION_MODE=optional` leaves guarded routes reachable until an
authorization backend is configured. `PHLO_AUTHORIZATION_MODE=required` makes
guarded routes fail closed with HTTP `503` when no backend is available.

Production (`PHLO_ENVIRONMENT=production`, `prod`, `staging`, or `regulated`,
or any regulated deployment) defaults to `required` and fails startup if you
explicitly configure `optional`. Development stays opt-in unless you set the
mode explicitly or enable regulated mode. Production requires an
authentication provider and an authorization backend; the preflight report
(`phlo services preflight --production`) verifies the locally inspectable part
of that contract.

You can also declare these settings in `phlo.yaml` via `api.authorization` or
`services.phlo-api.authorization`.

## Auto-Configuration

This package is **fully auto-configured**:

| Feature               | How It Works                                            |
| --------------------- | ------------------------------------------------------- |
| **Metrics Labels**    | Exposes Prometheus metrics at `/metrics`                |
| **Service Discovery** | Automatically scraped by Prometheus                     |
| **Health Check**      | Provides `/health` endpoint for container orchestration |

## Usage

```bash
# Start the API service
phlo services start --service phlo-api

# For local source development, initialize dev mode first.
phlo services init --dev --phlo-source /path/to/phlo
phlo services start --service phlo-api
```

## Endpoints

- **API Base**: `http://localhost:4000`
- **Health**: `http://localhost:4000/health`
- **Metrics**: `http://localhost:4000/metrics`
- **OpenAPI Docs**: `http://localhost:4000/docs`

## API Routes

| Route           | Description               |
| --------------- | ------------------------- |
| `/api/lineage`  | Data lineage queries      |
| `/api/quality`  | Quality check results     |
| `/api/assets`   | Dagster asset information |
| `/api/branches` | Nessie branch management  |
