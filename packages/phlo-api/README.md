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

## Explicit payload reclamation

Reclamation is irreversible and separate from archive. It requires an archived
record, write authorisation, `confirmed: true`, a non-blank `reason`, and the
current version, checksum, or revision digest. Provider tables, candidate
branches, catalogues, release records, and operation claims are not deleted.

All paths below have the `/api/v1` prefix and require `env`:

| POST path | Required precondition | Local content reclaimed |
| --- | --- | --- |
| `/metric-models/{model_id}/reclaim` | `expected_version` | Full draft definition |
| `/data-products/{product_id}/reclaim` | `expected_version` | Full draft definition |
| `/uploads/csv/{upload_id}/reclaim` | `expected_checksum` | Raw retained CSV bytes |
| `/transform/models/{model_id}/drafts/{draft_id}/{revision}/reclaim` | `expected_revision_digest` | Copied workspace inputs |
| `/transform/models/{model_id}/drafts/{draft_id}/{revision}/executions/{execution_id}/reclaim` | `expected_revision_digest` | Local execution workspace |

Metric and product definitions become compact permanent tombstones. Their IDs
cannot be reused, and later definition reads, exports, or restores return 410.
Reclamation frees one full-definition slot, not the retained identity evidence.
CSV and native metadata remain available, but reclaimed payloads cannot be
restored. Retained reports, checksums, SQL handoff evidence, and audit records
remain. Completed cleanup frees the corresponding payload quota. Interrupted
cleanup stays fenced until an explicit confirmed retry completes it.

Pending or unknown mutations block reclamation. The operation-store guard
holds the claim lock through local disposal, so a new claim cannot race the
safety check. Unknown persisted metadata also blocks disposal rather than
silently discarding a future fence or reference.

When `audit.retention_years` is configured, explicit reclamation also preserves
supporting payloads for that minimum period. Definition changes reset their
retention age. CSV and native payloads must satisfy the minimum from both
creation and their latest archival timestamp, so recent supporting activity
cannot rely on an old creation date. Audit records and tombstones do not expire
when payloads become eligible. An unset minimum does not bypass archive,
identity, authorisation, or unresolved-mutation checks.

## Load observation pagination

`GET /api/v1/loads?env=<environment>&limit=100` returns authorised private CSV
observations followed by ingestion runs. `limit` defaults to 100 and accepts
values from 1 to 300. The response includes `next_cursor`; passing that value
as `after` continues the inventory. A cursor belongs to the actor, environment,
Dagster location, and configured reference that created it.

CSV observations use descending receipt identity. Ingestion runs follow
Dagster's creation order, with assets ordered by ID within each run. This is
not a global ordering by load completion time. The continuation preserves a
partially returned run so runs with many assets do not lose observations.

Each request scans at most 100 provider runs. An empty page can still have a
`next_cursor` when unrelated or denied runs consume that scan. The inventory
is live, not a frozen snapshot. Refreshing includes new observations before
the current cursor. Observatory's filters and JSON export apply to loaded
observations, not pages that have not been fetched.

Pagination does not expand the underlying evidence. Asset materialisation
row-count history covers the last seven days. An ingestion run is not a
per-file receipt, and an unknown CSV receipt timestamp remains unknown.
