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
| `GET /api/v1/incident-policies?env=...&limit=...` | Page through explicit per-environment asset policies; cursors are environment-bound. |
| `PUT /api/v1/assets/{asset_id}/incident-policy?env=...` | Replace ownership and freshness SLA overrides, with required `Idempotency-Key` and numeric `If-Match`. An absent policy has version 0; successful writes increment the version. A null SLA means no override. |
| `GET /api/v1/activity?env=...&limit=...` | Read environment-scoped incident events. |

The stats endpoint counts records only. An empty result means no persisted incident records, not that upstream systems are healthy. Type-specific upstream detail is not synthesized by these routes.

## Dagster detector

`phlo_incident_signal_sensor` is included in framework-built Dagster definitions. Configure `PHLO_INCIDENT_API_URL` and `PHLO_DAGSTER_INCIDENT_LOCATION_ENV_MAP`, a JSON object mapping each Dagster code-location name to exactly `prod` or `staging`. The sensor reads real `ASSET_CHECK_EVALUATION` event-log records, resolves the linked run's repository code location, and sends failed checks to the authenticated incident API using the existing `phlo-orchestration` / `api:orchestrate` service identity. It identifies dlt contract violations only when the asset key starts with `dlt_` and the recorded check metadata source is `domain` or `pandera`; other failed checks are grouped as `failed_check`. The location mapping is mandatory: missing, unknown, or unmapped origin data stops processing rather than guessing an environment. The sensor uses stable event-storage identities as `evidence_id` and `Idempotency-Key`; a delivery failure leaves the cursor unchanged for retry. On first activation it starts check-event monitoring at the current event-log head and does not backfill historical check failures.

For freshness, the sensor pages only explicit per-environment asset policies with a positive SLA override. It considers only unpartitioned assets present in its Dagster repository definition, reads materialization evidence for the exact asset key, and requires a linked Dagster run with status `SUCCESS` and a repository location explicitly mapped to the same environment. Materializations from another environment are ignored; no incident is emitted unless a matching successful run is found within the bounded event scan. The signal identity includes the successful materialization event ID, so detector retries replay the same write and a later successful materialization creates a distinct breach identity if the asset becomes stale again.

## Remaining phase-2 gaps

Partitioned freshness remains unsupported because this phase does not define partition-specific SLA semantics. The detector uses Dagster's repository asset graph to exclude partitioned assets rather than treating a missing partition marker as evidence of an unpartitioned asset.

Nessie conflict detection remains unsupported: the current merge interface returns only a boolean, so a false result cannot distinguish a conflict from other merge failures. Abnormal runtime remains unsupported: the known Dagster `dagster/max_runtime` tag applies to an op, not a whole run, and no accepted same-job/environment baseline or run-level threshold policy exists. This detector slice does not cover all audit aggregate records outside Dagster asset-check events. These gaps prevent phase-2 acceptance and must be closed before the phase can be marked complete.

The phase-3 partial asset/overview API is documented in the [phase-3 reference](unified-api-phase-3.md). Its overview reads persisted incident counts and SLA-derived freshness, but does not yet provide complete run/audit evidence or claim phase-3 acceptance.
