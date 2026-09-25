# Phase-2 incident and activity API (partial)

This reference describes the phase-2 incident API slice. The routes use the Phase-1 authentication and environment selection contract. They are not a claim that issue #1035 phase 2 is complete.

## Storage

Apply `packages/phlo-api/src/phlo_api/sql/001_incidents.sql` explicitly to the intended PostgreSQL database before enabling these routes. The API does not run schema migrations at startup. If durable incident storage is unavailable, requests fail with 503. The migration adds incident, timeline event, signal-deduplication, idempotency, subscription, follow-up, and asset policy tables in the existing `phlo` schema.

## Routes

Every route requires exactly one `env=prod|staging` selector. Read requests require `run.read` or `asset.read`; writes require `run.manage` or `asset.manage`. Detail and mutation permissions bind the environment and incident or asset identity. Incident and activity lists use keyset cursors bound to the environment and list type, with a maximum page size of 500.

| Method and path | Behaviour |
| --- | --- |
| `GET /api/v1/incidents?env=...&limit=...` | List incidents for the selected environment. |
| `GET /api/v1/incidents/stats?env=...` | Count persisted incidents by status. |
| `GET /api/v1/incidents/{incident_id}?env=...` | Read incident state. |
| `GET /api/v1/incidents/{incident_id}/timeline?env=...` | Read persisted incident events in time order. |
| `POST /api/v1/incidents?env=...` | Record a signal with `asset_id`, `kind`, `title`, `evidence_id`, and real evidence. Replayed `(env, evidence_id)` returns its existing incident. |
| `PATCH /api/v1/incidents/{incident_id}?env=...` | Add a comment, assign an owner, acknowledge, or reopen. Unsigned resolution is rejected. |
| `PUT /api/v1/incidents/{incident_id}/subscriptions?env=...&subscribed=...` | Subscribe or unsubscribe the authenticated subject, with a required `Idempotency-Key`. |
| `GET /api/v1/incidents/{incident_id}/follow-ups?env=...` | List follow-ups. |
| `POST /api/v1/incidents/{incident_id}/follow-ups?env=...` | Create a follow-up with a required `Idempotency-Key`. |
| `PATCH /api/v1/incidents/{incident_id}/follow-ups/{follow_up_id}?env=...` | Mark a follow-up complete or reopen it, with a required `Idempotency-Key`. |
| `GET /api/v1/assets/{asset_id}/incident-policy?env=...` | Read explicit per-environment asset ownership and freshness SLA overrides. |
| `PUT /api/v1/assets/{asset_id}/incident-policy?env=...` | Replace ownership and freshness SLA overrides, with required `Idempotency-Key` and numeric `If-Match`. An absent policy has version 0; successful writes increment the version. A null SLA means no override. |
| `GET /api/v1/activity?env=...&limit=...` | Read environment-scoped incident events. |

The stats endpoint counts records only. An empty result means no persisted incident records, not that upstream systems are healthy. Type-specific upstream detail is not synthesized by these routes.

## Phase-2 gaps

This slice does not yet provide the Dagster detector/sensor or connect signal producers for failed checks, dlt contract violations, and Nessie merge conflicts. A safe freshness detector can use Dagster's asset freshness policy and latest materialization only after confirming that the asset repository location and the successful materialization run belong to the selected environment; partitioned assets need explicit partition freshness semantics. Runtime abnormality is unsupported unless an explicit policy can be compared to a like-for-like run; raw duration alone is not a breach. These gaps prevent phase-2 acceptance and must be closed before the phase can be marked complete.

Overview aggregation remains phase 3. Phase 3 should read `/api/v1/incidents/stats` per environment and combine the returned persisted counts with its independently sourced asset, run, and audit evidence; this phase does not mount `/overview`.
