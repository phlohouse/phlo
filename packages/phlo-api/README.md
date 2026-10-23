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

## Retained CSV candidate lifecycle

`GET /api/v1/uploads/csv?env=<environment>` lists the current actor's retained
uploads with their original filename, checksum, and timestamps. The optional
`include_archived=true` also returns archived uploads. Other actors' uploads
are not visible.

`POST /api/v1/uploads/csv/{upload_id}/archive?env=<environment>` accepts
`{"archived": true, "reason": "Superseded review"}`. Use `false` to restore the
upload. Archiving preserves the upload bytes, review recipe, generated source,
candidate receipt, run evidence, and mutation fences. It neither reclaims
disk space nor resets retained-byte and global collection limits. Archived
uploads cannot change review, prepare a destination, plan, or load.

The candidate endpoints below use the same upload path and environment:

- `GET .../candidate/review` returns immutable evidence and a stale marker.
  A changed base prevents release eligibility but permits a bounded preview
  when the exact candidate hash and snapshot still verify. Candidate drift
  or unavailable providers suppress rows rather than hiding the receipt.
- `POST .../candidate/reconcile` verifies the confirmed Dagster run, immutable
  plan identity, table snapshot, and reviewed row count before recording a
  loaded result. It never launches a run or retries a write. Without an exact
  run receipt, an uncertain launch remains unresolved. A partially prepared
  catalog can recover only from a complete recorded binding with no later
  branch writes. Reconciliation never adopts an unrecorded catalog.
- `GET .../candidate/handoff` exports the attested contract and workflow files
  with checksums and candidate evidence. The generated files remain
  candidate-specific until a source review adapts and commits them. Existing
  branch checks, trial merge, and signed merge remain separate authorised POST
  operations. Export does not publish source or promote candidate data.
