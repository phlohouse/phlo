# Unified API phase 4: jobs, schedules and runs

Phase 4 adds environment-scoped Dagster job, schedule and run resources to `/api/v1`. It is additive: the existing `/api/observatory` and `/api/loki` routes remain mounted for their current callers.

Every resource requires exactly one `env=prod|staging` selector. The API resolves it through `PHLO_V1_ENVIRONMENTS`; callers cannot supply a Dagster location or Nessie ref. Run evidence is returned only when both the configured location and exactly one matching `phlo/ref` tag agree. Wrong-location and wrong-ref records are omitted. Missing or duplicate ref tags on a run in the selected location fail closed with 503. Dagster outages return 503 and malformed responses return 502 rather than an empty success.

## Read resources

| Method and path | Behaviour |
| --- | --- |
| `GET /api/v1/jobs?env=...` | Lists up to 500 job definitions in the selected Dagster location, with description, associated asset keys, resource identity, and links to runs and incident discovery. |
| `GET /api/v1/jobs/{job_id}?env=...` | Returns one unambiguous job definition. Duplicate job names in different repositories are not collapsed. |
| `GET /api/v1/jobs/{job_id}/summary?env=...` | Counts statuses and groups measured run durations into fixed second ranges from at most 200 recent location/ref-verified runs. The output reports the number of job runs included; it is not a lifetime total. |
| `GET /api/v1/jobs/{job_id}/patterns?env=...` | Derives failed-run groups and slow-run outliers from at most 200 recent verified runs. A run is slow when its duration exceeds both 60 seconds and twice the median duration of successful runs in that sample. This is a read-time heuristic, not a persisted incident or detector. |
| `GET /api/v1/schedules?env=...` | Lists schedules in the selected location with their Dagster-reported state and associated job. |
| `GET /api/v1/jobs/{job_id}/schedules?env=...` | Lists schedules associated with one unambiguous job. |
| `GET /api/v1/runs?env=...&limit=...&job_id=...` | Returns up to 100 recent runs; `job_id` filters that bounded sample. |
| `GET /api/v1/runs/{run_id}?env=...` | Returns status, timestamps, duration, selected assets, and a run-log link after location/ref validation. |
| `GET /api/v1/runs/{run_id}/timeline?env=...&limit=...&after_cursor=...` | Reads at most 100 Dagster events. `next_cursor` is Dagster's opaque event cursor; use it to request the next page. |
| `GET /api/v1/runs/{run_id}/logs?env=...&limit=...&after_cursor=...` | Reads at most 100 run events as logs, returns the next opaque cursor, and reports whether the run is terminal. To follow a live run, poll with the returned `after_cursor` until `is_terminal` is true. |
| `GET /api/v1/maintenance-windows?env=...` | Returns the bounded operator-configured window list. If no configuration exists, returns `status=unavailable`; an absent source is not represented as an empty schedule. |

The API asks Dagster for at most 100 runs for run listing and at most 200 for per-job summaries/patterns. Dagster's run-list API does not return an explicit continuation cursor, so these endpoints expose only the recent bounded sample. Run events use Dagster's `eventConnection(afterCursor, limit)` paging contract. The opaque cursor must be passed back unchanged; the client can poll the logs endpoint while a run remains active.

`PHLO_V1_MAINTENANCE_WINDOWS` is optional JSON with separate `prod` and `staging` arrays. Each window has a unique `id`, ISO-8601 `starts_at` and `ends_at` timestamps with timezone offsets, and an optional `description`. The API accepts at most 100 windows and 64 KiB of configuration, rejects duplicate IDs and end times that are not later than start times, and does not auto-apply or mutate these windows.

## Guarded actions

| Method and path | Preconditions |
| --- | --- |
| `POST /api/v1/jobs/{job_id}/launch?env=...` | Requires `run.manage` policy, `lakehouse:operate`, a non-blank idempotency key, shared operation controls or the single-replica/process assertions, and the run-ref-tag operator gate. Defaults to dry-run; a live launch also requires `dry_run=false` and `confirmed=true`. The API pins the Dagster location/repository and tags the run with the selected environment and Nessie ref. |
| `POST /api/v1/schedules/{schedule_id}/pause?env=...` | Requires the same authorization, idempotency and deployment gates, explicit `confirmed=true`, and an `expected_status` matching the current Dagster schedule state. |
| `POST /api/v1/schedules/{schedule_id}/resume?env=...` | Same as pause. |
| `POST /api/v1/runs/{run_id}/cancel?env=...` | Requires the same authorization, idempotency and deployment gates, `expected_status=STARTED`, and explicit confirmation for a live action. |
| `POST /api/v1/runs/{run_id}/retry?env=...` | Requires the same authorization, idempotency and deployment gates, `expected_status=FAILURE`, and explicit confirmation for a live action. |

All mutating requests require a non-blank `idempotency_key`. Identical replays return the original result before re-reading mutable provider state; reuse for a different target or payload conflicts. The target digest includes the actor, action, environment, mapped ref, and expected status. State preconditions are checked inside the idempotent execution claim, so stale status returns 409 without invoking Dagster. Mutation outcomes are audited; an audit failure is surfaced rather than reported as success.

PostgreSQL-backed operation controls share idempotency, resource exclusion, and rate limits across workers and replicas. Without that configuration, actions require `PHLO_V1_ACTIONS_SINGLE_REPLICA=1` and `PHLO_V1_ACTIONS_SINGLE_PROCESS=1`. Those settings remain assertions, not replica discovery. `PHLO_V1_ACTIONS_REF_TAG_CONTRACT=1` is required in both modes; the operator must verify that mapped Dagster locations preserve the supplied `environment` and `phlo/ref` tags. The [production guide](../guides/run-in-production.md#configure-api-operation-controls-before-adding-workers) describes shared identities, migration, audit latency, and the file-backed restrictions that remain.

Authorization denials remain 403, stale preconditions 409, invalid requests 422, unavailable services or disabled operational gates 503, and malformed Dagster responses 502. The service does not claim compliance from these endpoints.
