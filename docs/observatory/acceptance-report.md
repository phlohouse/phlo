# Observatory acceptance report — MC-10 qualification

Qualification record for the complete browser-to-lakehouse operating loop
under `docs/roadmaps/observatory-real-lakehouse.md` (MC-10). Every scenario
ran against the disposable provider-backed stack — native `phlo-api` built
from this checkout, packaged `phlo-observatory` image, Dagster, Nessie,
Iceberg, Trino, PostgreSQL and MinIO — with no core provider interaction
mocked.

## Run identity

| Field | Value |
|---|---|
| Commit | `0da47b650f32b38c1c79e96f597396f50584817c` (dirty working tree) |
| Config digest | `82f8e34b0ef5851286846822f853778789cf6d035bdc52da3d0830666077d3c1` |
| Project | `phlo-accept-73508b3b` (disposable, deleted after the run) |
| Fixture | `wap-failure-lab@78ed5eb3d9126c0b1a76735cfe4b0ae4988579fb` |
| Environment | `staging` (static bearer auth + RBAC enforced) |
| Command | `PHLO_RUN_OBSERVATORY_ACCEPTANCE=1 uv run pytest tests/acceptance -q` |
| Result | **15 passed in 509.07s** — single continuous run, 75/75 recorded checks |
| Evidence | `.tmp/observatory-acceptance/0da47b650f32b38c1c79e96f597396f50584817c-dirty/*.json` + `../screenshots/` |

## Scenario results

| Scenario | Result | Independent proof |
|---|---|---|
| 01 connection | passed | Mission context reports `project=phlo-accept-ca5bc4e1 env=staging mode=live`; durable_state/run_evidence/auth/authorization all `ready`; packaged proxy enforces auth (operator 200, anonymous 401); bogus API path → 404; packaged app renders live project identity. |
| 02 empty project | passed | Iceberg catalog contains only `information_schema`/`system`; run list empty; the three registered datasets list with no instance data; browser datasets page renders no demo content. |
| 03 resource identity | passed | `dlt_sensor_batches` and multi-segment `wap_failure_lab.batch_summary` resolve distinctly through links, reload and requests; bogus dataset id → 404. |
| 04 healthy execution | passed | Browser launch → Dagster run `0430d3b6-f59f-42af-adac-fde27539cba8` (SUCCESS), tags bind logical run `f9ba8023…` to WAP branch `pipeline-run-f9ba8023…`; WAP report `promoted`/`merged`; Trino reads 12 released rows on main; run detail renders the real run id. |
| 05 quality failure | passed | Launch `a52216a3-…` → provider FAILURE; WAP report `failed`; candidate remains listed as Blocked and its preview is refused; main unchanged (only the retained WAP branch differs); run page renders failure. |
| 06 retry/cancel | passed | Cancel `5bd4be51-…` → provider CANCELED, same-key replay returns recorded outcome; retry of `f5449f79-…` dispatched child `5999def6-…` carrying `parent_run_id`; identical retry key produced no second provider run; canceled run visible in browser. |
| 07 release | passed | Held candidate `33772d90…` audited to `status=success` without merge; preview eligible with all six gates and digest `f3d59622…` while Nessie refs stayed byte-identical; browser preview rendered gates+digest; confirm → `promoted` at target rev `a7c3cf1c…`; Trino reads 10 released rows on main for 2026-08-22; completed release and post-promotion state render in the app. |
| 08 concurrency/staleness | passed | Duplicate dispatch under one idempotency key replayed run `be72e2f8-…`; held candidate `854585d8…` audited; bogus preview digest → `stale_preview`; two concurrent confirmations resolved `[already_promoted, promoted]` — exactly one merge; main has 8 rows for 2026-08-21. |
| 09 dependency outage | passed | Dagster outage surfaced as unavailable orchestrator; materialize refused (`accepted:false`) with no fabricated datasets; overview renders degraded state; GraphQL + code location recovered after restart. |
| 10 ambiguous outcome | passed | Submission `acc-ambiguous-dbf63d83f27b` reconciled to the same provider run `694c6b49-…` across an API restart — no duplicate launch; durable claim recorded `materialize_asset/completed`; run settled SUCCESS. |
| 11 authorization | passed | Anonymous and unknown tokens → 401; viewer GET 200 / POST 403 on retry, cancel and promotion; 32 audited ops recorded under `acceptance-operator`; empty/garbage/expired tokens → 401; proxied read under API outage → 503 (fails closed). |
| 12 persistence | passed | 16 durable claims, the audit log (23,062 bytes), 14 runs and 4 completed releases survive a full API + packaged-app restart; the packaged proxy resumes serving the same project. |
| 13 service operations | passed | 24 declared services listed; probe `trino` → `runtime_state=running`; API-dispatched `service:minio:restart` → `outcome=succeeded`; `phlo-accept-73508b3b-minio-1` container observed restarted (fresh `StartedAt`). |
| 14 packaging | passed | Built image serves the SPA shell and deep links; proxied datasets payload byte-equals the API's own; the shell references same-origin URLs only; packaged app renders live datasets through the authenticated entry path. |
| 15 load contract | passed | 45 deterministic runs seeded to the durable store; cursor pagination walked 8 pages / 45 rows with zero overlap and clean termination; 32 concurrent dataset reads all 200 in ~0.1s. |

## Screenshots

Under `.tmp/observatory-acceptance/screenshots/`:
`01_connection-overview`, `04_healthy_execution-healthy-run`,
`04_healthy_execution-materialize-preview`, `05_quality_failure-failed-check`,
`06_retry_cancel-canceled-run`, `07_release-release-preview`,
`07_release-confirmed-release`, `09_dependency_outage-degraded-source`,
`14_packaging-packaged-datasets` — covering the required healthy run, failed
check, release preview, confirmed release and degraded source captures.

## Known unsupported capabilities

- `durable_audit_sink` — the audit dependency reports `unconfigured`: the
  acceptance project writes the operations audit log to `.phlo/audit/` (which
  persistence scenario 12 verifies across restart), but no external durable
  audit sink is configured in this deployment.

## Notable defects found and fixed during qualification

- Held-release gap: the promotion sensor coupled audit and merge in one tick,
  so no audited-but-unpromoted state existed and `quality_evidence` never
  reached the WAP report — manual promotion was permanently ineligible. Fixed
  via `review_hold` launches, audit-evidence stamping, and a preview source
  gate that compares the live branch head to the audited `report.source_hash`.
- Releases UI selected `candidates[0]` unconditionally; row-click selection
  now drives the detail panel and preview.
- Runs pagination skipped one durable row per page and could interleave
  provider rows across pages; the durable spine now drives the cursor with
  provider rows merged into page 1 only.
- Service actions inherited `PHLO_ENVIRONMENT=staging` from the API process,
  tripping the CLI production guard on generated compose overrides; the child
  subprocess environment now strips process-only posture variables.
- `resolve_host`/`resolve_url` now return literal `127.0.0.1` on loopback
  fallback so dual-stack hosts cannot route to an unrelated `::1` listener;
  stale `localhost` expectations updated across 14 test modules.

## Caveats

- The run executed on a dirty working tree; the recorded commit carries the
  `-dirty` marker and the config digest covers the sanitized effective
  configuration actually used.
- In one earlier run on the same commit, the `/mission/releases/completed`
  read returned a transient non-envelope error (provider guard → 503 class)
  after the promotion had already verified on main. The scenario check now
  asserts HTTP 200 before reading the payload so a recurrence surfaces the
  real status; this run qualified the endpoint green end to end.
- Polaris snapshot promotion remains contract-covered only; a second live
  catalog provider is outside MC-10 scope per the roadmap.
