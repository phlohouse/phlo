# Phase-1 `/api/v1` reference

These four read-only routes implement the backend part of [issue #1035](https://github.com/phlohouse/phlo/issues/1035). The replacement Observatory client is separate. The [generated HTTP inventory](http-api.md) lists their methods and paths; legacy routes remain mounted.

Every request needs an authenticated principal and an allow decision from the configured policy backend, including the event stream. A missing identity returns 401, denial returns 403, and an unavailable policy returns 503. Failures use `{"error":{"code":"...","message":"..."}}` and an `x-request-id` header. No route accepts a caller-supplied location, ref, or backend URL.

## Environment mapping

The unified API supports exactly `prod` and `staging`. It has no environment creation route and does not provision services or code locations.

Set `PHLO_V1_ENVIRONMENTS` to a JSON object with both distinct mappings. Bind service health endpoints per environment with `service_health_urls`; these URLs are operator-owned and never accepted from callers. For example:

```json
{"prod":{"dagster_location":"production_jobs","nessie_ref":"main","compose_project":"phlo_prod","service_health_urls":{"trino":"https://trino.prod.example/health","catalog-api":"https://catalog.prod.example/ready"}},"staging":{"dagster_location":"testing_jobs","nessie_ref":"candidate","service_health_urls":{"trino":"https://trino.staging.example/health"}}}
```

Set the existing `DAGSTER_GRAPHQL_URL` and `NESSIE_URL` connection settings for the process. `NESSIE_URL` points to a Nessie REST v2 base such as `http://nessie:19120/api/v2`. The API requests `GET {NESSIE_URL}/trees/{ref}` and verifies that the response's `reference.name` matches the selected ref. Each `service_health_urls` entry maps a discovered service ID to its complete HTTP(S) health endpoint. Endpoints cannot include credentials, query strings, or fragments. Prod and staging bindings are selected independently; the same endpoint may be used by both when the operator intentionally observes a shared platform service. Probes issue GET requests with a three-second per-request timeout, at most eight concurrent requests, and a five-second total deadline. Unobserved probes at the deadline return `unavailable` with `reason: health_probe_deadline_exceeded`. 2xx responses are healthy, non-2xx responses are unhealthy, and transport failures are unavailable. A configured URL does not assert process state. Definitions without an environment binding remain unknown; disabled definitions remain inactive. When `compose_project` is set, Docker observations are scoped to that project. Missing, partial, identical, or invalid environment mappings return 503. `PHLO_ENVIRONMENT` remains the process security mode and never selects a request environment.

## Responses

| Request | Response |
| --- | --- |
| `GET /api/v1/me` | `{subject,principal_type,email,roles,permissions}`. `permissions` maps `prod` and `staging` to the policy-granted `service.read` and `run.read` actions. |
| `GET /api/v1/environments` | `{items:[{env,status}]}` for environments the principal can read. `status` is `available` only if the selected Dagster location and Nessie ref both respond with matching identity; otherwise it is `unavailable`. No access to either environment returns 403, not an empty list. |
| `GET /api/v1/services?env=prod\|staging` | `{env,dagster_location,nessie_ref,items:[{id,status,observed_at,response_time_seconds,runtime_state,definition_state,reason,health_origin}],next_cursor:null}`. `dagster_location` and `nessie_ref` describe the selected server-owned mapping. `health_origin` is the configured HTTP health origin, without URL paths, or null when no HTTP binding exists. It is not a database connection string. Discovered service names supply identities, not deployment evidence. Dagster, Nessie, scoped Docker containers, and explicitly bound health URLs provide observations. Unbound definitions report `unknown` with `reason: no_environment_binding`; failed health requests report `unavailable`, and non-2xx health responses report `unhealthy`. The list is ordered by ID and fails with 503 if discovery fails or exceeds 500 items. |
| `GET /api/v1/events?env=prod\|staging` | `text/event-stream` of changed `service.status` and `run.status` records. See below. |

`env` is required on services and events. Invalid, missing, or repeated values return 422. Extra query parameters on these two routes return 400. Responses contain UTC ISO 8601 timestamps with offsets, seconds as numbers, and machine status codes. The wire models live in `packages/phlo-api/src/phlo_api/v1_contract.py`.

## Event stream limits

Each connection checks both `service.read` and `run.read` for the selected environment. The stream polls live sources every two seconds for up to 60 seconds. It compares the latest 100 Dagster runs, scoped by the run's repository location, against its initial snapshot. It does not emit a historical run list. A run that falls outside those 100 results can be missed; clients must refetch their read models on reconnect. Service probes compare current snapshots with their initial snapshot.

Changed records use `event: service.status` with `{env,id,status,observed_at,response_time_seconds}` or `event: run.status` with `{env,run_id,status,observed_at}`. Each change has a connection-local `id:`; `: heartbeat` comments do not assert state. The stream does not retain history or support replay. A `Last-Event-ID` request returns 400 `resync_required`; fetch the read models and reconnect without that header. An initial source failure returns HTTP 502 or 503. After headers, a source failure emits a changed `service.status` if available, then `event: error` with the standard error envelope and closes. The stream sends no claim that an unreachable source is healthy.
