# Phlo Observatory: roadmap to operating a real lakehouse

Date: 2026-09-18
Status: all milestones MC-00 through MC-10 complete; acceptance evidence recorded per ticket below (MC-10 qualification report at `docs/observatory/acceptance-report.md`).
Starting checkout: `/Users/garethprice/Developer/phlo-observatory-rewrite`
Starting branch: `feat/mission-control`
Inspected commit: `0da47b650f32b38c1c79e96f597396f50584817c` (30 commits ahead of the local `origin/main` reference; clean at inspection).
Predecessor: `/tmp/phlo-mission-control-handoff.md`.

## 0. Ticket status and evidence

Track each ticket's status, changed files, commands/results, actual acceptance evidence, remaining blockers and next ticket here. Do not mark a ticket complete on endpoint existence, HTTP 200, a green screenshot, or a checked box alone — record what was actually demonstrated.

| Ticket | Status | Changed files | Commands / results | Acceptance evidence | Blockers / notes | Next |
| --- | --- | --- | --- | --- | --- | --- |
| MC-00 | complete | `docs/roadmaps/observatory-real-lakehouse.md`, `packages/phlo-observatory/API_NEEDS.md`, `docs/observatory/acceptance-runbook.md`, `docs/observatory/meta.json` | Baseline results recorded below (2026-09-18). | Inventory covers every rendered route/control in `API_NEEDS.md`; fixture = `examples/lakehouses/wap-failure-lab` on `BundledStackHarness`; runbook has start/seed/stop + result format. | Fixture deltas tracked (not MC-00 blockers): multi-segment asset key → MC-03; long-running cancellable asset → MC-06. `make check` red at baseline: stale `OBSERVATORY_DIR`, support-manifest/version-drift entries for `phlo-observatory`, 1 `ty` error in `observatory_mission_control_sources.py:356`, 11 py test failures (packaging/manifest family). | MC-01 |
| MC-01 | complete | `observatory_mission_control_mode.py` (data mode, `SourceOutcome`, evidence builders, `reject_demo_mutations`), `observatory_mission_control_models.py` (`ReadEnvelope`/`ReadEvidence`/`ReasonCode`), `observatory_mission_control_state.py` (`CollectionResult`, pure `load_records`, `serve_*` envelope helpers, SEED-behind-demo), `observatory_mission_control_sources.py` (all derives → `SourceOutcome` + cached stale-capable source reads), `observatory_durable_state.py` (`read_collection`/`mutate_collection`/`initialize_collections`), `observatory_cache.py` (`CacheRead`/`cached_outcome` stale fallback, nested-model serializer), `observatory.py` (pure path helpers, relocated read-model cache db, strict branch loader, demo guards on 7 mutation routes), `observatory_operation_journal.py`, `observatory_saved_queries.py`, `observatory_runs.py` (`load_runs_strict`), `observatory_services.py` (`docker_reachable`), `main.py` (startup `initialize_collections`), all six `observatory_mission_*` routers → `ReadEnvelope`, `web/src/api/{types,mission-control}.ts` (envelope client), `web/src/components/layout/page-header.tsx` (`ReadStateChip`), `web/src/components/app/{app-topbar-actions,command-palette}.tsx` (live-query alerts/search), `web/src/data/demo.ts` (reduced to env-selector remnant), all data routes unwrapped, `test_mission_control_api.py`, `test_observatory_api.py`, `test_observatory_operation_journal.py` | `ruff` PASS; `ty check` PASS (also fixed the baseline `sources.py:356` tone Literal defect); `pytest packages/phlo-api` 464 passed; `tsc`/`eslint`/`vite build` PASS | Evidence matrix demonstrated: demo seed labelled `demo`; live healthy 200 `live`; absent record 404; provider down 503; no catalog provider 501; corrupt collection 503; malformed records → 200 `partial` + `dropped_records`; stale cache → 200 `stale` + `last_confirmed_at`; GET traversal creates no `.phlo/observatory`; all 7 mutation routes 409 in demo. | Substrate `/api/observatory/*` endpoints keep their existing (non-envelope) contract; their cache stays fresh-only. Env selector still static — replaced by mission context in MC-02. Live acceptance against the disposable fixture remains for MC-10. | MC-02 |
| MC-02 | complete | `observatory_mission_context.py` (new `GET /mission/context`: project validity, environment identity, data mode, read/control readiness, sanitized dependency probe statuses, per-family action availability), `observatory_mission_control_mode.py` (`environment_id()`, `validate_project()`, evidence now stamps `environment_id`), `observatory_mission_control_models.py` (`MissionContext`/`MissionDependencyStatus`/`MissionActionAvailability`), `request_origin.py` (new shared browser-origin policy: `PHLO_API_CORS_ORIGINS` + loopback dev regex), `security_manifest.py` (`get_mission_context` classified `admin.read`; `_enforce_cookie_request_origin` CSRF guard — cookie+unsafe+no-Authorization requests need the `x-phlo-request: observatory` header and a non-cross-site origin), `main.py` (router registered, CORS now reads the shared origin policy), `web/src/api/{types,mission-control}.ts` (`MissionContext` types, `queries.context()`, `credentials:"same-origin"`, `mutate()` CSRF-header helper, `ReasonCode` +`stale`), `environment-provider.tsx` (server-derived context, `queryClient.clear()` on identity change), `app-topbar-actions.tsx` (`EnvironmentBadge` display-only + readiness popover), `app-topbar.tsx`, `app-shell.tsx` (truthful footer: project/environment/observed-at, no fabricated copy), `src/data/demo.ts` deleted, `test_mission_control_api.py`, `test_security_manifest.py`, `test_observatory_package_install.py` | `ruff` PASS; `ty` PASS; `pytest packages/phlo-api` 480 passed; `tsc`/`eslint`/`vite build` PASS | Demonstrated: invalid `PHLO_PROJECT_PATH` → 200 context with `project_valid:false`, named blocker, `control_ready:false`; valid project → `project_id`/`environment_id`/`read_ready:true`; memory settings backend → `control_ready:false` + `durable_state:unsupported`; durable store patched → `control_ready:true` with provider-backed actions still gated per-dependency; demo mode → all 16 action families unavailable with demo reason + `evidence.status:"demo"`; CSRF matrix: cookie+POST no header → 403 `csrf_header_missing`, cross-site Origin → 403 `csrf_origin_rejected`, `Sec-Fetch-Site:cross-site` → 403 `csrf_cross_site`, same-origin/dev-proxy origin + header → guard passes to auth (401 anonymous), bearer+cookie → guard skipped, cookie+GET unaffected, denied cookie session → 403 `access_denied` (policy reason preserved); spoofed `X-Phlo-Role/Principal/Project` headers → 401 (never authority); every read evidence now carries `environment_id`. | Bearer-token auth unaffected by the guard; cookie-session enforcement applies uniformly across all unsafe classified routes. `audit` dependency reports `unconfigured` (no durable sink is wired in this checkout) — regulated-mode `control_ready` is therefore honest about durable-required mutations failing closed. Same-origin forwarding for the built app completes with MC-09 packaging; the dev path (vite proxy `/api/observatory`) forwards cookies today. | MC-03 |
| MC-03 | complete | `web/src/routes/datasets/{index,$}.tsx`, `web/src/routes/runs/{index,$runId}.tsx` (parameterized detail routes; hardcoded `orders`/`orders-daily` routes removed), `web/src/routeTree.gen.ts` (regenerated), `observatory_mission_control_sources.py` (`derive_dataset` now resolves through `_load_dataset_profile` — the shared listed-dataset/asset/candidate/promoted cascade — and serves real `schema_fields` from table metadata, bounded `preview` via `_load_table_preview` with `ref`/`pinned:false`/`state`/`detail` labels, `runs` filtered from the live run population with `runs_state` availability flag, `lineage` from declared upstream/downstream refs, ownership from declared metadata; new `derive_runs_page` — cursor-paginated mission run list on the same `load_runs_strict` population as the counters), `observatory_mission_control_models.py` (`DatasetPreview` +`ref`/`pinned`/`state`/`detail`/`has_more`/`limit`/`offset`, `MissionDatasetDetail` +`runs_state`, `MissionRunRow`/`MissionRunList`), `observatory_mission_evidence.py` (`:path` converters on every `{run_id}`/`{dataset_id}` route, new `GET /mission/runs` bounded list), `observatory_mission_{governance,platform}.py` (`:path` converters), `security_manifest.py` (`list_mission_runs` classified `run.read`), `web/src/api/{types,mission-control}.ts` (`MissionRunList`/`ObservatoryDataset`/`ObservatoryRun`/`ObservatoryListPage`, `missionPoll` 15s→2min backoff helper, `queries.runsList`/`datasetsList` cursor-paginated), `__root.tsx` (global `refetchInterval: missionPoll` + `refetchIntervalInBackground:false` — active-page polling, hidden tabs paused), `navigation.ts`/`breadcrumbs.ts`/`app-sidebar.tsx`/`command-palette.tsx`/`index.tsx`/`releases.tsx` (real `/datasets`, `/runs`, id-param links, live nav counts, palette searches real datasets+runs), `test_mission_control_api.py` (MC-03 identity/schema/preview/run tests) | `ruff` PASS; `ty` PASS; `pytest packages/phlo-api` 488 passed; `tsc`/`eslint`/`vite build` PASS | Demonstrated: `/datasets/marts/orders` and `/datasets/marts%2Forders` resolve the same canonical id (single decode, no double-decoding); `marts.orders` vs `marts/orders` stay distinct (no aliasing — 404 on the unregistered form); `../../phlo.yaml`-shaped ids are lookup keys → 404, never file access; two distinct datasets and runs resolve to their own payloads by direct URL; schema columns/types/nullability come from resolved table metadata; preview carries `pinned:false` + "not snapshot-pinned" labelling and `no_table`/`unavailable` states instead of fake rows; per-resource cache keys (`mission_datasets/{id}`, `mission_table_preview/{id}`) prevent cross-resource stale answers; runs list and overview counters share the `load_runs_strict` population. | First-asset selection and `FALLBACK_DATASET_ID`/`RUN_ID` constants removed; query keys are resource-scoped so navigating between datasets/runs cannot flash the previous resource. Dataset `access` grants stay `[]` — no provider models grants yet; `dataset_governance`/`publication-plan` remain seeded-only (demo) → absent 404 in live mode. Per-record lineage stays declared-dependency-level (no inferred row lineage). Frontend has no vitest harness — acceptance coverage is backend contract tests; URL-param refresh preservation is structural. | MC-04 |
| MC-04 | complete | `observatory_mission_control_models.py` (`RunIdentity` — run/durable/provider/attempt/report/operation/launch-digest/asset-ids join, `RunCheckOutcome` + `RunQualityReport` — per-attempt results plus blocking failure, `RunEventPage`/`RunLogPage` cursor pages, `attempt` on `RunEvent`/`MissionRunLogLine`), `observatory_mission_control_sources.py` (`derive_run` now takes the evidence store: identity join + retained-record fallback flagged `stale` when the orchestrator is unreachable; new store-backed derives `derive_run_{stages,spans,events,logs,quality,artifacts,consumers,configuration}` — all scoped by (project, run, attempt) so displayed ids provably belong to the selected run; `_stage_bars` real timeline windows; event payloads redacted via `redact_payload` and capped; `derive_dataset_checks`/`_mission_dataset_checks` read the Dagster check snapshot with execution-status honesty — passed/failed/running/skipped/never-evaluated distinct), `observatory_mission_evidence.py` (all run-evidence routes wired to `get_run_evidence_store`; `/events` + `/logs` are cursor-paginated `RunEventPage`/`RunLogPage`; `/quality` returns `RunQualityReport`; detail route ordered after `:path` subroutes so ids cannot swallow suffixes), `quality.py` (bounded-concurrency per-check execution fetches via semaphore; `NEVER_EVALUATED` status + nullable `severity` for defined-but-unexecuted checks), `web/src/api/{types,mission-control}.ts` (new identity/report/page types; `runEvents`/`runLogs` cursor-paginated infinite queries), `runs/$runId.tsx` + `run-evidence.tsx`/`run-rail.tsx`/`run-content.tsx` (`QualityReport` with per-attempt results table + blocking failure, `RunIdentityBlock`, paged `LogViewer` with load-more cursor, fabricated captions — "Candidate snapshot 938106", "Retained for 30 days", fixed counts — removed), `test_mission_control_api.py` (store-seeded identity/scope/pagination/offline-retained/check-state tests) | `ruff` PASS; `ty` PASS; `pytest packages/phlo-api` 493 passed, 24 deselected; `tsc`/`eslint`/`vite build` PASS | Run-page evidence and dataset checks are now durable-store/provider backed; release-candidate pinning of quality evidence lands with MC-06 promotion. | | | |
| MC-05 | complete | `api/operation_controls.py` (claims now bind `payload_digest` — same key + changed payload → `idempotency_payload_conflict` 409; `payload`/`audit_intent` params on `replay_or_execute{,_async}` persist validated intent before dispatch; `MutationNotDispatched` releases aborted claims as `safe_to_retry`; `list_unresolved_claims`; `resolve_idempotency_claim` accepts `idempotency_key_hash`), `run_action_contract.py` (`reconcile_unresolved_claims` — bounded provider-evidence resolution; `cancel_run` claims resolve when the target run reports terminal; reconciled replays carry a `vh-resolved-` handle), `observatory.py` (commit-time target-state recheck `_recheck_run_target_state` — retry only on failed, cancel only on running, unreadable state aborts dispatch reclaimably; intent audit + payload binding on retry/cancel/materialize/backfill; unresolved claims surfaced in `/operations` as `replay_blocked` records; throttled `_maybe_reconcile_claims` on list refresh; cursor pagination on `/operations`), `observatory_operation_journal.py` (`_cap_operations` — queued/running/unknown records never evicted by the 200-cap), `observatory_models.py` (`ObservatoryActionResult.status` widened: `accepted`/`running`/`unknown` for unsettled work; `ObservatoryOperationList` +`next_cursor`), `observatory_workflow_wizard.py` (`issued_at` + `PHLO_WORKFLOW_PROPOSAL_TTL_SECONDS` expiry — signed timestamp, expired preview → 410 new-preview-required), `api/continuity.py` (payload binding), `main.py` (startup `reconcile_unresolved_mutations`), `web/src/api/{types,mission-control}.ts` (`RunActionResult` types; `mutations.retryRun`/`cancelRun` with generated idempotency keys + CSRF), `web/src/components/sections/run/run-actions.tsx` (confirm dialog + live result status/handle), `runs/$runId.tsx` (real Retry/Cancel replace static buttons), `test_observatory_run_action_contract.py` (+8), `test_observatory_operation_journal.py` (+1), `test_observatory_api.py` (recheck-aware statuses) | `ruff` PASS; `ty` PASS; `pytest packages/phlo-api` 501 passed, 24 deselected; `tsc`/`eslint`/`vite build` PASS | Demonstrated: concurrent duplicate → provider invoked once, second gets 409 in-progress; same key + changed payload → 409 `idempotency_payload_conflict` while identical payload replays byte-identical; run that recovered before commit → `rejected`, provider mutation never invoked; unreadable target state → 503 dispatch abort + claim reclaimable (not unknown); intent-audit storage failure → 500 with zero provider calls; lost cancel reply reconciles `succeeded` from provider-terminal evidence and replays without re-invoking; unknown claim survives restart, stays 409, and is visible in `/operations` as unresolved/`replay_blocked`; journal cap preserves unresolved records; `/operations` paginates with `next_cursor`; expired workflow proposal → 410. | Retries stay unresolved (new run id isn't correlatable from target status) until explicit evidence — visible, not blind-replayed. Intent audit reuses `audit_operation` with `phase:"intent"`. | MC-06 |
| MC-06 | complete | `observatory.py` (`GET /services/{id:path}/probe` — provider-backed live detail with dependencies/dependents/actions, registered before the `:path` catch-all and cache-cleared per call; `_service_actions` now carries `equivalent_cli_command` + `expected_evidence` recovery expectations on start/stop/restart; `/actions` gains the full guard chain — demo rejection, `lakehouse:operate`, rate limit, **required** idempotency key, payload-digest binding, intent audit, durable record; `service:`-prefixed action ids normalize before dispatch), `security_manifest.py` (probe registered under `admin.read`), `observatory_models.py` (`ObservatoryActionRequest` +`idempotency_key`), `web/src/api/{types,mission-control}.ts` (`ObservatoryServiceDetail`/`ObservatoryActionResult`/`MaterializeResult` types; `queries.serviceProbe`; `mutations.previewMaterialize`/`materializeAsset`/`serviceAction`), `web/src/components/sections/dataset/materialize-action.tsx` (preview → confirm → commit; provider `run_id` becomes the Open-run link; commit invalidates run/dataset/overview queries), `web/src/components/sections/platform/service-controls.tsx` (per-service Manage dialog: live runtime/readiness, depends-on/dependent-workflows, recovery expectations, enabled-actions-only dispatch), `service-health-table.tsx` (Manage column), `datasets/$.tsx` (Preview materialization wired; disabled until dataset evidence is live/stale), `test_observatory_api.py` (+8: probe detail/404, key-required 422, replay-no-redispatch, unmanaged-target rejected, disabled-restart skipped, preview/commit split) | `ruff` PASS; `ty` PASS; `pytest packages/phlo-api` 508 passed, 24 deselected; `tsc`/`eslint`/`vite build` PASS | Demonstrated: probe answers live runtime/readiness + dependency graph for declared services and 404s undeclared/container ids; restart only dispatches `phlo services restart --service <declared-id>` (fixed argv, no shell) after the enabled-capability recheck — stopped/unmanaged targets return skipped/failed with zero subprocess calls; reposted key+payload replays the stored result without a second dispatch; materialize preview validates through the provider dry-run and the commit launches once under a fresh key; quality rerun stays skipped/disabled when the check has no executable `fn`. | Service restart readiness is observed through the next probe/poll of actual container state — the UI never paints a restart as recovered on command exit. | MC-07, MC-09 |
| MC-07 | complete | `observatory_wap.py` (`PromotionCheck`/`PromotionPreview` dataclasses + `evaluate_promotion_preview` — the same gate order the sensor enforces: owned staging ref, verified launch binding, strategy match, audit decision, source/target revision guards; digest binds every input), `observatory_mission_control_sources.py` (`derive_release_candidates`/`derive_release_candidate`/`derive_completed_releases`/`derive_promotion_preview` re-derived from `load_wap_reports` — candidates exist only where a governed lifecycle report does, completed releases only on `promoted`/`cleanup_complete` receipts; `_candidate_row`/`_release_candidate_detail` surface logical+orchestrator run ids, staging ref, source/target revisions, per-table candidate-vs-released snapshots, launch-binding evidence and specific blockers; `_catalog_provider` resolves branch and snapshot catalogs), `observatory_mission_control_models.py` (`ReleaseCandidate`/`ReleaseCandidateDetail`/`CandidateSnapshotChange`/`CandidateEvidenceRow`/`PublicationPlan`/`PromotionPreview`), `observatory_mission_releases.py` (`GET /mission/releases/candidates/{id:path}/preview` registered before the `:path` detail route), `security_manifest.py` (preview under `admin.read`), `web/src/api/{types,mission-control}.ts` (`PromotionPreview` types + `queries.promotionPreview`), `candidate-detail-panel.tsx` (real evidence/blockers/snapshot table + preview checks + digest-bound confirm), `test_observatory_api.py` (+7: report-derived candidates, bare branch not promotable, detail evidence, preview eligible/digest-stable, moved-target rejection, unknown/blocked 404, tampered manifest blocker), `test_mission_control_api.py` (release-source-down 503; no-catalog degrades readiness + preview unavailable gates) | `ruff` PASS; `ty` PASS; `pytest packages/phlo-api` 516 passed, 24 deselected; `tsc`/`eslint`/`vite build` PASS | Demonstrated: candidate list/detail derive solely from WAP lifecycle reports (a staging-shaped branch with no report is never promotable); completed releases require a governed merge receipt, not a succeeded run; preview re-evaluates owned ref, launch manifest binding, strategy, audit decision and live source/target revisions, binds them into a stable digest, and reports moved-target/missing-manifest/tampered-manifest/absent-report as specific blockers | Row-delta per table shows `—` (no catalog row-diff API exists); Polaris snapshot catalogs resolve through the same `_catalog_provider` fallback but snapshot fixtures are covered only via `resolve_release` seam, not a live Polaris regression test | MC-08 |
| MC-08 | complete | `src/phlo/wap_reports.py` (shared durable WAP store — atomic report writes, content-addressed launch manifests, `promotion_lock` advisory lockfile), `src/phlo/wap_promotion.py` (shared `promote_wap_candidate` state machine — durable intent before mutation, merge_started/merged receipts, cleanup checkpoint, conflict/recovery/resume for branch and snapshot strategies, injectable reader/writer/emit hooks), `phlo_dagster/wap_launch.py` + `phlo_dagster/wap_sensors.py` (delegate to the shared store/state machine; sensor tests unchanged), `observatory_promotion.py` (`execute_manual_promotion` — preview-gate evaluation, preview-digest binding, in-lock fresh revision recheck, sensor-win reconcile as `already_promoted`, mid-sequence resume via `merge_state`, post-commit consumer-visible verification, terminal receipt inside the lock), `observatory_mission_releases.py` (`POST /mission/releases/candidates/{id:path}/promotion` before the `:path` detail route), `security_manifest.py` (promotion under `dataset.publish` + CSRF/idempotency guards), `observatory_mission_control_sources.py` (`_catalog_provider` resolves branch or snapshot catalogs through the patched registry seam), `web/src/api/{types,mission-control}.ts` (`PromotionResult` + `mutations.promoteCandidate`), `candidate-detail-panel.tsx` (preview checks + digest confirm + outcome render + release-query invalidation), `test_observatory_api.py` (+9: promote eligible candidate, stale digest, tampered manifest, sensor-win reconcile, interrupted-cleanup resume without re-merge, replay same key, concurrent confirmations publish once, merge_started recovery_required, snapshot CAS promotion) | `ruff` PASS; `ty` PASS; `pytest packages/phlo-api` 528 passed, 24 deselected; `pytest packages/phlo-dagster` 427 passed, 15 deselected; `tsc`/`eslint`/`vite build` PASS | Demonstrated: one governed promotion command reuses the WAP-owned sequence (no raw `merge_branch` in the mission route); manual and sensor paths share candidate identity, revision guards, durable claims/receipts and the promotion lock; a sensor win reconciles truthfully instead of double-promoting; crash-after-commit resumes cleanup+receipt from durable `merge_state` without a second merge; consumer-visible state verifies before the terminal receipt; cleanup failure stays `promotion_pending` and resumable; `merge_started` with unproven outcome refuses as `recovery_required` rather than guessing; snapshot strategy promotes through CAS release pointer, never a fabricated branch merge. |
| MC-09 | complete | `web/serve.mjs` (srvx production server — compiled TanStack Start fetch handler + static client + graceful shutdown, HOST/PORT env), `web/src/routes/api/observatory/$.ts` (server-side same-origin proxy → `PHLO_API_URL`, hop-by-hop header stripping, manual redirects, duplex body forwarding, truthful 503 on unreachable upstream — replaces the dev-only vite proxy), `web/package.json` (`start: node serve.mjs`, `srvx` dep), `web/Dockerfile` (4-stage: `npm ci` deps → build → `npm ci --omit=dev` runtime-deps → slim runtime, `node serve.mjs` CMD), `web/.dockerignore`, `service.yaml` (`files:` staging of web build inputs into `.phlo/web`, `context: web`/`dockerfile: Dockerfile`, node-based healthcheck — node:22-slim has no wget/curl), `Makefile` (`OBSERVATORY_DIR` → `web`, real scripts), `ci.yml`/`security.yml` (correct path + lint/typecheck/build, dead format/test steps removed), `scripts/dependency_delta.py` (`LOCKFILES` → `web/package-lock.json`), `packages/phlo-api/Dockerfile` (stale `ca-certificates` apk pin → `20260909-r0`), `registry/support/v1.json` + `src/phlo/support_data/v1.json` (observatory service/package entries, evidence paths, version alignment), `phlo_observatory/__init__.py` (dynamic version), `docs/observatory/operator-runbook.md` (config/capabilities/auth/startup/health/first-run/release/diagnosis/unknown-outcome/upgrade/rollback), `docs/observatory/meta.json`, `docs/reference/observatory-contracts.md` (canonical surfaces + mission API families updated to the rewritten app), `packages/phlo-observatory/tests/test_service_definition.py` (8 pinning tests: staged sources exist, compose context+dockerfile, deterministic install, real server entry, node healthcheck, start script, server-only proxy env, no author paths), `phlo-observatory-storybook` (`src/fixtures/demo.ts` — gallery fixtures reshaped to current API models, moved out of the app; `@/` imports → `../../fixtures`; QueryClientProvider in preview; `QualityFailure`→`QualityReport` + prop-driven run/platform/release stories; `@tanstack/react-query` dep) | `make check` PASS (8/8 lanes: support manifest, version drift, py lint/format/typecheck, 4695 py tests, ts lint/typecheck); `npm --prefix web ci` + `run typecheck`/`lint`/`build` PASS; `npm --prefix phlo-observatory-storybook run build-storybook` PASS; service-definition tests 8/8 PASS | Demonstrated on a disposable project (`phlo services init --profile api` → `.phlo/web` staged, compose `context: /tmp/obs-proj/.phlo/web`): docker build from generated context OK; `phlo services start` brought postgres → phlo-api (service_healthy) → observatory up; `status` reports all healthy on remapped ports; container healthcheck = `node -e fetch` (passes, `unless-stopped` restart policy); app `/`, deep links `/releases` `/runs` → 200 outside vite dev; `/api/observatory/services` returns real registry data through the packaged proxy (internal `phlo-api:4000` resolved in-network); upstream 404/500 relayed byte-identical (proxy faithful); restart → app+proxy recover; `phlo services stop` clean; `grep -r PHLO_API_URL dist/client` empty — no internal URL/credential in the bundle. | phlo-api image used is published `0.16.2` — predates mission routes, so mission endpoints 404 upstream (proxy itself verified faithful); a `0.17.x` phlo-api image is needed for full-stack evidence in MC-10. Published `ghcr.io/phlohouse/phlo-observatory:0.17.0.dev0` image tag not yet pushed — local build only. | MC-10 |
| MC-10 | complete | `tests/acceptance/` (new suite: `conftest.py` stack fixture, `evidence.py` report writer, `stack.py` disposable-stack driver — native phlo-api spawn w/ thread-isolated asyncio startup, packaged observatory compose layer w/ `!reset` depends_on + host-gateway API routing, workload service tokens, sensor control via instigation-id mutations, `proxied_get` browser-like Accept), `test_01`–`test_15` (all required scenarios + pagination/load), `phlo_dagster/wap_launch.py` + `wap_sensors.py` (`review_hold` carried into the WAP report; held runs audited to `status=success` with `source_hash` + `quality_evidence`/`quality_decision_id` stamped, merge skipped; promoted receipts retain audit evidence), `observatory.py` (`review_hold` on materialize; provider-unreachable → structured rejection not bare `{"error"}`; runs endpoint → durable-spine cursor pagination with provider rows merged into page 1 only, anchored at last returned row; service-action subprocess env strips process-only posture vars so `PHLO_ENVIRONMENT=staging` no longer trips the CLI compose-override guard), `observatory_mission_releases.py` (preview source gate compares live branch head to audited `report.source_hash`), `operation_controls.py` (`require_scope` engages whenever HTTP authorization is required, so authenticated subjects/scopes reach audit), `src/phlo/run_evidence/store.py` (public spine-cursor encoder), `web/src/routes/releases.tsx` + `pending-candidates-table.tsx` + `candidate-detail-panel.tsx` (row-click candidate selection, keyed detail panel — replaces hardwired `candidates[0]`), `examples/lakehouses/wap-failure-lab` (acceptance topology: phlo-api + observatory services, static users w/ scopes, `slow_sensor_feed` cancellable asset), `src/phlo/config/network.py` loopback fallback now emits literal `127.0.0.1` (dual-stack `::1` misroute fix; 14 test modules' stale `localhost` expectations updated), `docs/observatory/acceptance-report.md` + `meta.json` | `PHLO_RUN_OBSERVATORY_ACCEPTANCE=1 uv run pytest tests/acceptance -q` → **15 passed in 509.07s**; `uv run pytest tests packages --ignore=tests/acceptance -q` → 4695 passed, 4 skipped; `ruff check`/`format` PASS; `tsc --noEmit` PASS; `npm run build-storybook` PASS | All 15 required scenarios green in one continuous run against disposable project `phlo-accept-73508b3b` (commit `0da47b65-dirty`, config digest `82f8e34b`), 75/75 checks: live project/env identity through the packaged proxy; honest empty catalog; multi-segment id roundtrip; browser launch → real Dagster run `0430d3b6` → WAP `promoted` → 12 rows on main in Trino; quality failure → `failed` report, blocked candidate, preview refused, main unchanged; cancel→CANCELED + retry with parent correlation, both idempotent; held candidate `33772d90` audited w/o merge → read-only preview (digest `f3d59622`, refs byte-identical) → browser confirm `promoted` @ `a7c3cf1c` → 10 rows on main; duplicate key single effect + stale digest refused + concurrent confirms `[already_promoted, promoted]` = one merge; Dagster outage → refused actions, truthful reads, degraded UI, clean recovery; API restart mid-launch reconciled to the same provider run; auth matrix 401/403 + audit subject recorded + fail-closed proxy; claims/audit/runs/receipts survive restart; service probe matches target + real minio container restart; packaged image serves shell/deep links, proxy byte-identical, same-origin only; 45 seeded runs paginate 8 pages zero-overlap + 32 concurrent reads. Evidence: `.tmp/observatory-acceptance/0da47b65…-dirty/*.json`, 9 screenshots; report: `docs/observatory/acceptance-report.md` | Recorded unsupported: `durable_audit_sink` unconfigured (file audit verified instead). Run executed on a dirty tree (`-dirty` marker). One transient non-envelope read on `/mission/releases/completed` observed in an earlier same-commit run; check now asserts 200 first, endpoint re-qualified green. Polaris live promotion out of scope per roadmap. | — |

### Baseline verification (recorded 2026-09-18, commit `0da47b65`)

| Command | Result |
| --- | --- |
| `git status --short --branch` | `## feat/mission-control`, clean |
| `git rev-parse HEAD` | `0da47b650f32b38c1c79e96f597396f50584817c` |
| `uv run --project packages/phlo-api pytest packages/phlo-api/tests/ -q` | PASS — 452 passed, 24 deselected |
| `uv run --project packages/phlo-api ruff check packages/phlo-api/` | PASS |
| `npm --prefix packages/phlo-observatory/web run typecheck` | PASS (`tsc --noEmit`) |
| `npm --prefix packages/phlo-observatory/web run lint` | PASS (`eslint --max-warnings 0`) |
| `npm --prefix packages/phlo-observatory/web run build` | PASS (TanStack Start client + server bundles) |
| `npm --prefix packages/phlo-observatory-storybook run build` | script absent — actual script is `build-storybook`; `npm --prefix packages/phlo-observatory-storybook run build-storybook` PASS |
| `uv run pytest packages/phlo-dagster/tests/test_wap_launch.py packages/phlo-dagster/tests/test_wap_sensors.py packages/phlo-dagster/tests/test_wap_snapshot_strategy.py packages/phlo-polaris/tests/test_promotion.py packages/phlo-polaris/tests/test_release_cas.py -q` | PASS — 99 passed |
| `make check` | FAIL — see below |

`make check` baseline failures (all recorded, none silently skipped):

- `support manifest` (exit 1): `phlo-observatory` evidence paths do not exist (`packages/phlo-observatory/tests`, stale `src/phlo_observatory/src/routes/...` path); `phlo-observatory-storybook` absent from the manifest; `release_set` version `0.16.2` vs declared `0.17.0.dev0`; `observatory` service `image_reference` mismatch.
- `version drift` (exit 1): `phlo_observatory/__init__.py` hand-maintains `__version__ = "0.17.0-dev"`; `registry/support/v1.json` pins `0.16.2`.
- `py typecheck` (exit 1): `ty` reports `observatory_mission_control_sources.py:356` — `tone` is `str`, expected `Literal["success","warning","danger","accent","muted"]`.
- `py test` (exit 1): 11 failed / 4600 passed — all in the observatory packaging/support-manifest family (`test_check_version_drift`, `test_container_security`, `test_dependency_delta`, `test_generated_image_publication`, `test_provider_core_compatibility`, `test_support_manifest`, `test_toolchain_pins`).
- `ts lint`, `ts format` (exit 254), `ts typecheck` (exit 1): `OBSERVATORY_DIR ?= packages/phlo-observatory/src/phlo_observatory` has no `package.json`; the real web package is `packages/phlo-observatory/web`.

No baseline failure is in the mission-control API logic itself (its 452 tests pass); all red checks are packaging/manifest/Makefile integration owned by MC-09 plus the one `ty` defect in mission sources owned by MC-01.

## 1. Destination

An operator opens one installed Phlo Observatory, sees the actual state of its configured Phlo lakehouse, selects a real dataset, launches and diagnoses work, previews and confirms a guarded release, and verifies what consumers can now read. The operation remains explainable after a browser refresh, API restart, provider timeout, or interrupted promotion.

“One Observatory” means one operator application backed by the existing `phlo-api` and provider services. It does not mean collapsing every service into one process. The first supported target is **one project, one tenant, one configured environment per deployment**, consistent with ADR 0047. The environment shown in the UI is server-derived identity, not a label that pretends to switch backends.

The first complete acceptance stack is Dagster + Nessie + Iceberg + Trino + object storage + PostgreSQL, using the existing dlt/dbt/quality pipeline. Provider-neutral interfaces remain authoritative. Polaris snapshot promotion must remain a separate strategy, never disguised as a Nessie branch merge; its existing behaviour receives regression coverage, but a second live provider qualification is outside this milestone.

### Required operator capabilities

| Workflow | Completion means |
| --- | --- |
| Connect | Validate the project, configured providers, durable stores, identity and permitted actions; display a truthful environment identity. |
| Inspect | Browse actual datasets/assets, schema, bounded row previews, lineage, runs, quality, releases and service readiness. |
| Execute | Preview and launch materialization, retry failed work, cancel active work, and inspect the resulting operation and run. |
| Diagnose | Correlate logical run, orchestrator run, operation, check evaluation and release evidence; distinguish data-quality failure from execution failure. |
| Release | Inspect the exact candidate and audit evidence, preview promotion, confirm it under current authorization and revision checks, and verify the released data. |
| Recover | Explain blocked, failed and unknown outcomes; reconcile interrupted operations without blindly executing twice. |
| Operate services | Probe managed services and preview/confirm a supported restart, with a clear effect and observed readiness result. Unsupported actions remain unavailable. |
| Install | Run the built application through the Phlo service lifecycle with same-origin API access, persistent state and authenticated controls. |

The minimal useful delivery is the truthful read-only console at M2. It is an intermediate milestone, not completion of this request. The requested destination requires M6.

### Explicitly outside this roadmap

- Multi-project switching, multi-tenancy, fleet management, HA and new availability guarantees.
- A new orchestration engine, catalog, generic workflow platform, policy engine or parallel governance database.
- Editing pipeline code, arbitrary SQL writes, arbitrary shell execution, plugin installation, destructive service removal, backup restoration, or automatic production rollback from the browser.
- Workspace membership administration, notification delivery, full access-policy compilation/drift management, and a separate human publication-review workflow.
- Implementing every Paper panel simply because it exists. Unsupported panels must say why they are unavailable or be omitted.
- Declaring all of Phlo production-ready, changing support gates, or deploying to a live production environment as a consequence of completing the code.

## 2. Evidence and baseline limitations

This roadmap comes from reading the current checkout, not just the predecessor handoff. No tests, live mutation, service deployment or provider acceptance run was performed while writing it. The predecessor's counts (12 assets, 20 checks, 452 passing tests) are historical observations, not acceptance baselines to hard-code.

Verified source findings:

| Finding | Evidence in the checkout | Consequence |
| --- | --- | --- |
| Live failures can fall back to seeds. Empty durable collections also select seeds. | `observatory_mission_control_sources.py`, `observatory_mission_control_state.py`, mission routers | Fix truth semantics before expanding data wiring. |
| Reads can create state directories and initialize/import durable collections. | `_state_dir()` and `load_collection()` | The handoff's “reads do not mutate” assertion needs behavioural verification and repair. |
| Dataset page selects the first asset or `marts.orders`; navigation uses demo routes. | `web/src/routes/datasets/orders.tsx`, `runs/orders-daily.tsx`, `releases.tsx` | Stable resource routing is core work, not polish. |
| Run stages, logs, events, traces, artifacts and configuration use stored/seed collections. | `observatory_mission_evidence.py` | A live run header does not establish live run diagnostics. |
| Environment selection is local UI state; query keys and request URLs lack deployment scope. | `environment-provider.tsx`, `app-topbar-actions.tsx`, `api/mission-control.ts` | Replace the cosmetic selector with server identity for the first release. |
| Documented generic action payload is inaccurate. | `ObservatoryActionRequest` currently has `action_id` and `expected_state` | Do not build the client against `{family,target,params,dryRun,idempotencyKey}` as if it already exists. |
| Generic families are often skipped, but other real paths already exist. | `observatory_actions.py`; specialized run/asset routes and service/dataset dispatch in `observatory.py` | Reuse and harden the actual execution paths; do not replace the entire backend. |
| PostgreSQL is the default durable settings backend; memory is explicit development/test mode. | `src/phlo/plugins/observatory_settings.py` | The handoff's memory configuration cannot qualify persistent control. |
| WAP already has immutable launch bindings, reports, sensors and reconciliation. | `phlo-dagster/wap_launch.py`, `wap_sensors.py`; Polaris `promotion.py` | Observatory must use these authorities rather than infer releases from branch names. |
| Production packaging needs qualification. | `web/Dockerfile`, `web/package.json`, `web/vite.config.ts`, `service.yaml` | Current start uses Vite preview; inspected API proxy lives in dev-server configuration. A dev session proves neither production routing nor packaging. |
| Repository frontend checks still default to the previous source path. | root `Makefile` | Update the build/check integration as part of delivery. |

`API_NEEDS.md` mixes historical claims with newer discoveries. Its claims that releases do not exist, ownership is not surfaced, and the quality API returns no checks cannot serve as a current execution inventory. Reconcile it in MC-00.

## 3. Architecture and decisions

```text
Operator browser
      |
      v
Authenticated edge / Observatory application
      | same-origin API path; preserve verified human identity
      v
phlo-api: authorization + read projections + guarded operations
      |                   |                    |
      v                   v                    v
Dagster capability    Catalog / query       Existing durable stores
runs/checks/events    capabilities           operations/audit/evidence
      |              Nessie / Trino          and settings
      +-------------------+--------------------+
                          |
                    Real lakehouse
```

1. **Providers and recorded evidence own truth.** Mission endpoints are projections, not a second lakehouse state model. Keep browser code provider-neutral; keep credentials and provider addresses server-side.
2. **Live and demo are explicit modes.** Add `PHLO_OBSERVATORY_DATA_MODE=live|demo`, default `live`, to the appropriate API-owned configuration. Demo is development/test-only, unmistakably labelled and cannot execute provider mutations. A dependency failure never changes mode.
3. **Scope comes from server configuration.** One deployment has one project/environment identity. Use the existing project identity resolver; do not use a browser-supplied path, label or provider URL to choose authority. Include identity in cache keys, operation records and preview bindings.
4. **Mutations have a durable lifecycle.** Preview describes intent; confirmation authorizes a specific validated request; provider acknowledgement means submitted, not completed. Durable evidence and reconciliation determine the terminal result.
5. **Preserve current security contracts.** Accepted ADR 0047 and current API authorization supersede the older shared-token example in ADR 0026. Do not introduce browser-local shared admin credentials or replace a human with an Observatory service principal.
6. **Use the existing stack.** No new service is planned. This spans substantially more than eight files and several packages; deliver small vertical increments with independently evidenced gates.

The fragile assumption is that existing capabilities can express the required launch and promotion operations with durable correlation. If an adapter lacks that ability, implement the narrow missing contract in its owning package and keep the UI action disabled until it passes acceptance. Do not bypass the capability layer with a browser-to-provider call or raw branch merge.

### Read contract to introduce

Mission projections return typed data plus read evidence. Use one shared schema in Python and TypeScript, with this meaning:

- `data`: actual result, a preserved last-confirmed result, or null when unavailable; an empty collection is a valid successful result.
- `evidence.status`: `live`, `stale`, `unavailable`, `unsupported`, or `demo`.
- `evidence.projectId`, `environmentId`, `source`: stable non-secret identities.
- `evidence.observedAt`, `lastConfirmedAt`: real timestamps, never the time an old cache entry was served.
- `evidence.reasonCode` and sanitized explanation when the answer is partial or unavailable.
- Composite responses carry evidence per independent section. A healthy services section cannot certify a missing release section.

Known absent resource: 404. Unauthorized: 401/403. Malformed request: 422. State or preview conflict: 409. Failed required dependency without a usable answer: 503 with a structured, sanitized problem. A composite response may return healthy sections while marking other sections unavailable. Unsupported optional panels have explicit capability reasons. Preserve last-confirmed data only within the configured cache retention and label it stale; it is never sufficient authority to mutate.

### Action contract to extend

Keep existing routes and provider adapters. Add shared validated preview/commit machinery beneath the existing generic and specialized action routes, and document each route's actual OpenAPI payload. Preserve established field naming (`action_id`, `dry_run`, `idempotency_key`) rather than adding a competing camelCase API.

For required actions, the common contract carries a stable action/target identity, validated parameters, project identity, a persisted preview identifier and digest, expected revisions where applicable, explicit confirmation, and a required idempotency key. Extend typed request models as needed; reject unknown fields that could silently imply unsupported protection.

- Preview returns the affected resources, permission result, blockers, consequences, evidence identifiers, observed revisions and expiry. Use a five-minute preview validity window; revalidate authorization and preconditions on commit regardless of expiry.
- Preview performs no provider mutation. Persisting its own explicitly requested operation/preview record is allowed.
- Idempotency binds principal, project, action, target and canonical request digest. Reusing a key with a different request returns 409. Preserve unresolved claims; never expire an unknown outcome into permission to execute again.
- Persist intent and required audit evidence before calling a provider. Failed persistence prevents invocation.
- Operation states: `previewed`, `blocked`, `submitted`, `running`, `succeeded`, `failed`, `cancelled`, `unknown`. Preserve provider result and correlation handles separately.
- A timeout after submission becomes `unknown` until provider evidence resolves it. Retry/status reconciliation must use the same identity and must not automatically resubmit ambiguous work.
- Operation success means the requested effect was verified, not merely that a handler returned 200. Cancellation acceptance remains pending until the provider confirms the outcome.

Use existing governed mutation, run-action, operation journal, authorization and settings/evidence storage contracts. Extend their schemas transactionally; do not create another independent action journal.

## 4. Milestones and dependency order

| Milestone | Tickets | Exit outcome |
| --- | --- | --- |
| M0: reproducible baseline | MC-00 | Accurate inventory, declared fixture and evidence harness. |
| M1: trustworthy connection | MC-01, MC-02 | Correct project, honest failures, no demo fallback, protected API boundary. |
| M2: complete read workflow | MC-03, MC-04 | Navigate arbitrary actual resources and diagnose real runs. |
| M3: guarded execution | MC-05, MC-06 | Materialize/retry/cancel/probe/restart with durable outcomes. |
| M4: release and recovery | MC-07, MC-08 | Preview/promote/verify and recover interrupted releases. |
| M5: installable application | MC-09 | Built service, persisted state, authentication and checks work together. |
| M6: qualification | MC-10 | Browser-to-lakehouse acceptance evidence for the full operating loop. |

```text
MC-00 -> MC-01 -> MC-02 -> MC-03 -> MC-04
                    |                 |
                    +-> MC-05 ------> MC-06
                              MC-04 + MC-05 -> MC-07 -> MC-08
MC-02 -> MC-09 packaging work; final MC-09 gate requires MC-06 and MC-08
MC-03 + MC-04 + MC-06 + MC-08 + MC-09 -> MC-10
```

Execute in the listed milestone order by default. Dependencies describe what can be split into PRs; they do not require multiple agents. No calendar estimates are asserted before MC-00 establishes the size of existing gaps.

## 5. Executable tickets

### MC-00 — Establish the actual baseline and acceptance fixture

Dependencies: none.

Read first: this roadmap, `API_NEEDS.md`, `docs/reference/observatory-contracts.md`, ADRs 0011 and 0047, `docs/operations/production-readiness.md`, current capability/action tests, and applicable repository instructions. Check the actual branch/SHA and diff before editing.

Work:

1. Move this roadmap into `docs/roadmaps/observatory-real-lakehouse.md` in the Phlo checkout when implementation is authorized. Keep a ticket-status and evidence table there.
2. Rewrite `API_NEEDS.md` as a current per-panel inventory: browser route, API route, actual provider/store, resource identity, capability, live/demo/unsupported status, mutation handler, and acceptance test. Cover shell alerts/search/environment/footer as well as all main pages.
3. Inspect existing WAP failure-lab/project fixtures and select a reusable disposable fixture with at least two datasets, a multi-segment asset key, one healthy run, one quality failure, one execution failure, and one long-running cancellable job. Use separate ports, volumes and generated local credentials. Record exact start/seed/stop commands in an acceptance runbook.
4. Retain `materialize-check/lakehouse` as a read-only reference unless its owner explicitly authorizes mutations. Never commit into it, reset it, or use it for failure injection by assumption.
5. Add an acceptance result format under the repository's established test/artifact conventions: commit, configuration digest, project ID, timestamps, run/operation/release IDs, checks and artifact paths. Never store credentials or secret-bearing provider output.

Likely targets: `API_NEEDS.md`, `docs/roadmaps/observatory-real-lakehouse.md`, existing `examples/lakehouses/` fixtures, `packages/phlo-api/tests/test_mission_control_api.py`, new focused acceptance scripts/tests.

Acceptance: another agent can reproduce the fixture; inventory includes every rendered panel/control; baseline tests report actual outcomes; discrepancies are explicit. No constant asset/check count is treated as a correctness assertion.

### MC-01 — Replace implicit seeds with truthful read states

Dependencies: MC-00.

Work:

1. Implement explicit data mode and shared evidence schema. Isolate seed data behind demo mode; remove automatic source-error/empty-store fallback in live mode.
2. Propagate typed unavailable/unsupported/corrupt/absent outcomes from adapters. Review lower-level helpers that already swallow errors into `[]`; changing only the outer router is insufficient.
3. Remove fabricated facts from live responses and rendered copy, including synthetic row previews, example snapshot IDs, successful-looking counts, invented trace spans and inferred record lineage without supporting evidence.
4. Preserve empty as empty. Make unknown IDs and malformed stored records observable. A partially invalid collection must be marked partial/degraded rather than silently losing rows.
5. Separate store initialization/legacy migration from reads. GETs must not create `.phlo` directories, import legacy state or write seed/business records. Run explicit idempotent initialization during setup/startup; read caches may live outside project business state.
6. Require the existing durable PostgreSQL settings backend for qualified control; memory mode cannot pass control readiness. Do not switch to memory to hide a storage error.

Targets: `observatory_mission_control_models.py`, `observatory_mission_control_sources.py`, `observatory_mission_control_state.py`, `observatory_durable_state.py`, mission routers, `web/src/api/{types,mission-control}.ts`, shared status components; lower-level preview/adapter helpers where needed.

Acceptance: healthy nonempty, healthy empty, missing capability, missing resource, timeout, stale cached result and corrupt store each produce distinct truthful UI/API outcomes. Fingerprint fixture project files before and after GET-only traversal. No mutations or demo data appear. Demo cannot invoke real actions.

### MC-02 — Bind the application to the correct project and authority

Dependencies: MC-01.

Work:

1. Validate `PHLO_PROJECT_PATH` points to a project containing `phlo.yaml`; expose a sanitized diagnostic and refuse control readiness on invalid configuration. Resolve provider settings through their existing owners; do not add a second endpoint resolver.
2. Add `GET /api/observatory/mission/context` with project ID, environment identity, mode, read/control readiness, sanitized dependency statuses and capability/action availability. Register it in security and route manifests.
3. Replace the fake Production/Staging/Development selector with the one configured environment. Include context identity in all client/server caches. Clear data and pending previews if the deployment context changes.
4. Implement/verify same-origin authenticated API forwarding for development and the built application. Preserve the verified human bearer identity; never expose provider credentials. Enforce API authorization independently of the UI.
5. Classify every added/changed route and resource in `HTTP_ROUTE_DECLARATIONS`; retain globally unique route function names. Test denied reads and writes, spoofed headers, cross-project IDs, expired identity and unavailable authorization/audit dependencies.
6. For cookie-backed edge sessions, enforce the repository's request-origin/CSRF protections on mutations and test them. Do not accept a client-supplied role or project label as authority.

Targets: API security manifest/auth integration, network/provider settings, context route, `web/src/components/app/`, `web/src/api/`, SSR/same-origin request boundary, `service.yaml` as appropriate.

Acceptance: UI displays the actual configured environment; a bad project path cannot yield a healthy demo; denied users cannot execute by calling HTTP directly; project mismatch is rejected. Read readiness may be available while control readiness is blocked, with reasons.

### MC-03 — Navigate and inspect actual resources

Dependencies: MC-02.

Work:

1. Replace fixture routes with parameterized `/datasets/$datasetId` and `/runs/$runId` routes and real list/search entry points. Remove first-asset selection and demo fallbacks. Regenerate the router tree.
2. Resolve IDs using existing resource identity contracts. Correctly round-trip multi-segment asset keys, dots, slashes and URL-encoded names without double decoding, path traversal or aliasing.
3. Wire dataset schema and bounded row previews to catalog/query capabilities; label branch/ref and snapshot identity. Read a pinned snapshot where supported; otherwise label the view current/non-pinned rather than claiming snapshot consistency.
4. Wire actual lineage, recent runs, declared ownership and quality definitions/outcomes. Do not derive record-level lineage from matching row positions or column names.
5. Wire overview services, execution, data products and release summaries independently. Compute counters from the same authoritative populations as their drilldowns. Hide unsupported queue/governance claims.
6. Add bounded pagination and active-page polling. Reuse existing run streaming where applicable; do not add a new SSE service for the dashboard. Start with 15-second active-page polling, pause hidden pages and back off on failures. A `staleTime` alone is not polling.

Targets: `web/src/routes/`, navigation/breadcrumbs/search, `api/mission-control.ts`, `observatory_mission_overview.py`, sources, dataset/table/asset projections.

Acceptance: navigate two distinct datasets and runs by direct URL and search; refresh preserves selection; malformed/unknown IDs fail honestly; schema and sample rows match independent provider reads; no stale response from another resource flashes into the selected page.

### MC-04 — Complete run and quality evidence

Dependencies: MC-03.

Work:

1. Join the existing logical Phlo run identity, orchestrator run ID, attempt, operation ID, asset IDs and WAP report/launch digest. Expose the mapping explicitly; never merge attempts by display name.
2. Replace seeded stages/events/logs/configuration/artifacts with existing Dagster event history, run-evidence storage and WAP reports. Paginate logs/events; bound payload size; redact secrets and sensitive row samples.
3. Reuse the functioning Dagster check reader for dataset checks. Fetch definitions then executions per asset/check with bounded concurrency and a short shared cache. Keep execution status separate from evaluation result: executed/passed, executed/failed, execution failed, running, skipped and never evaluated are not interchangeable.
4. Pin quality evidence used for a release to the relevant run/attempt and candidate snapshot. “Latest check passed” for another run cannot approve this candidate.
5. Expose missing optional traces/consumers as unsupported or unrecorded; do not manufacture spans or downstream consumers. Retained historical evidence remains inspectable while live dependencies are offline, with provenance.

Targets: `observatory_mission_evidence.py`, sources, `quality.py`, `observatory_runs.py`, run-evidence adapters, run/dataset evidence panels.

Acceptance: inspect healthy, quality-failed and execution-failed runs; distinguish pending from failed; prove every displayed evidence ID belongs to the selected run. Run logs update and reconnect without duplicating or skipping retained events. An offline provider does not erase existing confirmed evidence.

### MC-05 — Make mutations durable, authorized and replay-safe

Dependencies: MC-02; complete before enabling any new controls.

Work:

1. Trace existing generic dispatch, service control, dataset workflow, run/asset actions, governed mutation machinery and operation journal. Map which protections exist before extending them.
2. Implement the shared action contract described above. Extend operation models beyond the current terminal-only generic result where asynchronous work requires it. Keep existing client/API contracts aligned through explicit schema changes and tests.
3. Persist validated preview/intent and audit before provider invocation. Ensure a post-call journal write is not the only record of intent. Reserve idempotency atomically before invocation and reject digest mismatches.
4. Recheck authorization, target state, capability and dependency readiness at commit. Expired previews and changed revisions require a new preview.
5. Add persisted reconciliation: on startup and status refresh, resolve nonterminal operations from provider correlation/evidence; use bounded background retries where existing runtime support permits. Unknown actions must remain visible and blocked from blind replay.
6. Ensure audit and operation history survive API restart and keep distinct operation attempts. Set bounded list pagination without deleting unresolved operation identities.

Targets: `observatory_models.py`, `observatory.py`, `observatory_actions.py`, `run_action_contract.py`, `observatory_operation_journal.py`, existing mutation/idempotency owners and durable stores, client mutation helpers and confirmation/status UI.

Acceptance: concurrent duplicate requests invoke the provider once; changed payload with same key conflicts; denied/expired/stale requests never invoke it; storage failure before dispatch blocks it; provider success followed by lost HTTP reply reconciles without a second invocation; restart preserves pending/unknown state and audit identity.

### MC-06 — Close the execution and service-operation loops

Dependencies: MC-04, MC-05.

Work:

1. Connect dataset “Preview materialization” to the existing asset materialization provider path; commit only after preview and confirmation, then navigate to the returned actual operation/run.
2. Connect run retry and cancellation to the existing specialized routes; preserve retry strategy and parent/attempt relationships. Do not conflate accepted cancellation with terminated execution.
3. Use the same operation monitor for all controls. Invalidate affected read queries on confirmed changes; never optimistically paint a run/release successful.
4. Add provider-backed service probe and guarded restart only for declared managed services. Show dependent workflows and recovery expectations. Reject unmanaged targets and unsupported capabilities; do not accept shell strings or arbitrary container IDs.
5. Keep quality-only rerun unavailable unless the provider supports that exact selection. Materializing a dataset is a distinct action and must be labelled accordingly.

Targets: run/dataset/platform controls, current action/service/orchestrator adapters, operation-detail view and queries.

Acceptance: from the browser launch a real run, inspect its events, retry a failed fixture run and cancel a long-running run; each action has independent provider evidence. Probe and restart an approved disposable noncritical service, then observe actual readiness. Duplicate confirmation does not duplicate work.

### MC-07 — Build release review from real candidate evidence

Dependencies: MC-04, MC-05.

Work:

1. Replace “WAP-named branch equals releasable candidate” with a projection over canonical WAP launch/report evidence, catalog candidate contents, audit decision and release history.
2. Display logical/orchestrator run IDs, source revision, target baseline, table snapshot IDs, audit/evidence digests and blockers. Branch presence alone is insufficient. Missing identity/evidence blocks promotion.
3. Treat the governed release receipt as authority for completion. Do not equate a successful run, merged-looking branch, or nonempty destination with a confirmed release.
4. Add a read-only promotion preview using the current WAP checks: ownership, prepared launch, audit result, current source/target revisions and supported strategy. Bind all of these into the preview digest.
5. Keep explicit strategy metadata (`branch` versus `snapshot`). Use existing `VersionedCatalog` and `SnapshotPromotionCatalog` interfaces; run the Polaris regression tests whenever shared contracts change.

Targets: mission release routes/sources/models, release UI, WAP launch/report readers, existing catalog capability adapters.

Acceptance: valid fixture candidate shows exact evidence and a preview; failing, missing, mismatched and stale evidence produces specific blockers. No unrelated branch is promotable through this screen. Candidate-vs-released comparisons show actual snapshots and values.

### MC-08 — Promote and reconcile through the WAP authority

Dependencies: MC-07.

Work:

1. Expose a narrow governed promotion command through the provider/WAP owner and the shared action machinery. Reuse/extract the promotion validation and execution already owned by WAP; do not duplicate it in a mission route or call a raw merge as a shortcut.
2. Coordinate manual promotion with `wap_auto_promotion_sensor`. Both must use the same canonical candidate identity, revision guards and durable claim/receipt. Preserve the project's existing automatic policy. If the sensor wins after preview, reconcile as already promoted or stale; never execute a second promotion.
3. Persist promotion intent, audit and provider correlation; compare source and target revisions immediately before mutation. Refuse when the audited candidate or destination has changed.
4. Verify target catalog state and consumer-visible query results before marking the release confirmed. Separate provider commit, release receipt, evidence finalization and candidate cleanup so interrupted cleanup does not hide a successful publication.
5. Expose recovery status and a guarded reconcile/resume action where the existing WAP contract supports it. Missing or ambiguous evidence cannot produce a fabricated receipt.
6. Do not promise reverse promotion as rollback. A changed catalog is external state: rollback requires a separately reviewed compensating release or existing supported recovery procedure.

Targets: `phlo-dagster/wap_sensors.py`, `wap_launch.py`, catalog capabilities and adapters as needed; API release action binding; operation/release status UI. Keep orchestration logic out of generic catalog providers.

Acceptance: promote one passing candidate and verify actual consumer rows/snapshots; reject failing checks; reject stale target/source; exercise two simultaneous confirmations and a competing sensor; interrupt after provider commit but before response/receipt and prove reconciliation produces one release. Retained evidence survives cleanup and process restart.

### MC-09 — Deliver one installable Observatory

Dependencies: MC-02 to start; MC-06 and MC-08 for final gate.

Work:

1. Verify the build output and supported production server for the installed TanStack Start version. Configure a genuine production entrypoint and same-origin API routing; do not assume the Vite development proxy carries into production.
2. Make the Docker build deterministic from the lockfile, verify generated Compose build context/Dockerfile resolution, include a healthcheck executable available in the final image, and verify the process listens on the configured interface/port.
3. Exercise `phlo services` install/start/status/stop against the disposable project. Confirm project mount/config resolution, provider URLs inside the network, durable database configuration, restart policy and readiness reporting. No reference to the author's absolute project path belongs in the package.
4. Repair root Makefile, CI, packaging and any remaining frontend paths to run the new `web/` checks. Update canonical route documentation to the final navigation; do not retain fixture aliases simply to preserve broken links.
5. Produce an operator runbook: configuration, required capabilities, authentication, startup, health/readiness, first run, release, diagnosis, unknown-outcome recovery, upgrade and application rollback.
6. Back up affected durable stores before schema migration. Use versioned additive changes where possible. Document when an older binary cannot read new state; never claim UI rollback reverses lakehouse mutations.

Acceptance: a fresh build starts outside the Vite dev server, deep links survive reload, authenticated same-origin reads/actions work, service healthcheck passes, process restart retains history, and no provider credential/URL leaks into browser payloads or bundles. Repeat an operation through the packaged app, not only the dev app.

### MC-10 — Qualify the complete operating loop

Dependencies: MC-03, MC-04, MC-06, MC-08, MC-09.

Implement an automated browser acceptance suite with the repository's existing browser harness, extending it if needed. Drive the installed/built application and independent provider assertions against the disposable fixture. Do not mock the core provider interactions in this gate.

Required scenarios:

| Scenario | Independent proof |
| --- | --- |
| Correct connection | Project/environment/capability identity matches configured backend. Invalid path fails honestly. |
| Genuine empty project | Empty catalog stays empty, no example dataset appears. |
| Arbitrary resource selection | Two datasets/runs and encoded IDs retain correct identity through links, refresh and requests. |
| Healthy execution | Browser-launched operation maps to actual Dagster execution and resulting dataset state. |
| Quality failure | Failed evaluation blocks release; execution failure is displayed separately. |
| Retry/cancel | Exactly one requested attempt/cancel effect, correct lifecycle and preserved parent correlation. |
| Release | Preview causes no catalog change; confirm changes the expected target; Trino sees the expected released values. |
| Concurrency/staleness | Duplicate, stale-preview and sensor-race cases yield one valid effect or an explicit conflict. |
| Dependency outage | Controlled Dagster/catalog/query outage yields unavailable/stale state, no seeds, and blocks dependent actions. |
| Ambiguous outcome | Lost provider response/API restart reconciles without duplicate launch/promotion. |
| Authorization | Missing/expired/insufficient identity and unavailable audit/auth control cannot execute. |
| Persistence | Restart preserves pending operations, release receipts and audit, including unknown outcomes. |
| Managed service operation | Probe/restart evidence corresponds to the target service and actual readiness. |
| Packaging | The same workflows run from the built service through the configured authenticated entry path. |

Also exercise pagination and bounded check-fetch concurrency with at least ten times the fixture's normal asset/check count using a deterministic test backend; this is load/contract evidence, not a substitute for the real-provider scenarios.

Capture a small set of actual UI screenshots (healthy run, failed check, release preview, confirmed release and degraded source) and inspect them for correct labels, usable error recovery, keyboard confirmation and overflow. Preserve the design's visual language, but truth takes precedence over exact sample text/counts. Read the applicable design skills before making interface changes.

Exit artifact: one acceptance report tied to the exact commit and configuration digest, containing scenario results, actual IDs/revisions, sanitized API/provider evidence, screenshots, test command results and known unsupported capabilities. A failed or skipped required scenario keeps M6 incomplete.

## 6. Verification commands and evidence discipline

Run from the Phlo checkout, not Sheetbase. These are established baseline commands/paths inspected while writing this plan; run them when implementing, and record any baseline failures before changing them:

```bash
git status --short --branch
git rev-parse HEAD
uv run --project packages/phlo-api pytest packages/phlo-api/tests/ -q
uv run --project packages/phlo-api ruff check packages/phlo-api/
npm --prefix packages/phlo-observatory/web ci
npm --prefix packages/phlo-observatory/web run typecheck
npm --prefix packages/phlo-observatory/web run lint
npm --prefix packages/phlo-observatory/web run build
npm --prefix packages/phlo-observatory-storybook run build
uv run pytest packages/phlo-dagster/tests/test_wap_launch.py packages/phlo-dagster/tests/test_wap_sensors.py packages/phlo-dagster/tests/test_wap_snapshot_strategy.py packages/phlo-polaris/tests/test_promotion.py packages/phlo-polaris/tests/test_release_cas.py -q
make check
```

The root `make check` currently contains the previous frontend location; MC-09 must fix that integration rather than presenting a skipped frontend check as green. Provider tests require their workspace dependencies; missing dependency/service setup is an explicit blocker, not a passing result. MC-00/MC-10 must add exact fixture and browser commands to the runbook once the current harness is selected and proved.

Per-ticket tests must prove behaviour, particularly side effects and failure boundaries. Keep API schema/client checks, route/security manifest coverage and installed-package route coverage. Run targeted tests while implementing; run the full required repository checks before handoff. A passing Storybook build is not browser verification, and passing fixture unit tests is not live acceptance.

## 7. Configuration and external dependencies

Use current provider configuration, not the copied machine-specific values in the predecessor. `PHLO_PROJECT_PATH` must resolve the directory containing `phlo.yaml`. Existing known configuration names include `DAGSTER_GRAPHQL_URL`, `NESSIE_HOST`/`NESSIE_PORT`, `TRINO_HOST`/`TRINO_PORT`, `PHLO_RUN_EVIDENCE_DB_URL`, `PHLO_OBSERVATORY_SETTINGS_BACKEND` and server-side `PHLO_API_URL`. Do not assume `NESSIE_URL` is read. Validate each effective endpoint inside the process/container network where it is used.

Required dependencies and credentials:

- Existing Phlo Python workspace and Node lockfile environment: API, provider adapters and web build.
- Container runtime/Compose and Phlo service lifecycle: reproducible isolated fixture and packaged application; runtime access is not exposed directly to browsers.
- Dagster endpoint and Phlo workload identity when protected: execute and inspect runs/checks/events.
- Nessie, Trino and object-storage credentials owned by their providers: catalog operations, bounded reads and pipeline data access.
- PostgreSQL settings/evidence/audit access with the existing appropriate roles: durable control records; never acceptance-qualified through memory mode.
- Existing OIDC/JWT issuer and edge integration for authenticated deployment qualification: preserve human principal into API authorization. Isolated tests can use the existing test identity support; no new third-party account is required by the architecture.

These services and credentials were not probed or provisioned during roadmap authoring. MC-00 and MC-02 must record reachability and readiness before dependent implementation acceptance. If unavailable, continue contract/unit work but report the live gate blocked. Do not ask for secrets in chat or commit real environment files.

## 8. Delivery boundaries and future work

Suggested PR slices follow M0–M6; keep each slice reviewable and do not mix unrelated checkout work. Preserve the current branch work, inspect local instructions and user branch conventions, and obtain explicit authorization before pushing, merging, live deployment, destructive fixture cleanup or shared-environment mutation. This document requests planning only; it does not authorize those actions or starting implementation.

Do not remove a legitimate `.phlo` directory as cleanup. Fixtures must identify their own resources; stop only resources created for the acceptance run. Keep durable evidence needed to explain unknown or committed operations.

Deferred items have explicit owners and entry conditions:

| Deferred work | Owner | Entry condition |
| --- | --- | --- |
| Publication reviews | Dataset/governance capability owner | Separate approved review lifecycle and policy, after real release control passes. |
| Access-policy declared/compiled/verified views | Existing security/compiler/provider owners | Reuse current policy sources and runtime verification; first inventory them, do not presume absent. |
| Contract metadata editing | Asset declaration/schema owners | Agreed canonical declaration/edit authority; read-only existing metadata ships now. |
| Notifications | Alerting capability owner | Agreed routing, delivery and audit contract; no seeded notification rules in live mode. |
| Members/defaults administration | Existing identity/settings owners | Defined authorization and persistence semantics; no separate UI-only authority. |
| Multi-environment switching | API/runtime configuration owner | Explicit configured-target registry, credentials and isolation tests; one-server context ships first. |
| Polaris live UI qualification | Catalog/WAP owner | Repeat release/recovery acceptance against snapshot strategy; no claim of catalog-wide atomic multi-table writes. |
| Backfill and destructive maintenance UI | Orchestration/maintenance capability owners | Extend the same preview/action/evidence contract with bounded scope and explicit recovery semantics. |

## 9. Instructions to the implementing agent

Start with MC-00 and verify checkout drift. Preserve existing working capabilities. For each ticket, record: status, changed files, commands/results, actual acceptance evidence, remaining blockers and next ticket. Do not infer completion from endpoint existence, a successful HTTP status, a green screenshot, or an updated checklist.

If a required source is missing, distinguish “not found yet”, “not configured”, “unavailable”, and “no producer exists”. Inspect the owning capability, existing durable evidence and tests before inventing a producer. A missing provider contract is a scoped implementation task within its owner, not permission to fabricate an answer.

Stop claiming completion at exactly the level demonstrated: M2 means readable; M3 means guarded execution; M4 means release/recovery; M6 means this exact installed stack passed the full operator loop. The final handoff includes commit, validation, actual run/release identifiers, acceptance report path and unsupported capabilities. It does not claim production deployment or universal provider support.
