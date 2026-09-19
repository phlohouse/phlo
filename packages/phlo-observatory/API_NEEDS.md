# Mission Control — per-panel API and data inventory

Rewritten 2026-09-18 under `docs/roadmaps/observatory-real-lakehouse.md` MC-00.
This is the **current** inventory: every rendered browser panel/control mapped
to the API route it calls (or the demo module it still reads), the real
provider/store behind that route, its resource identity, its truth status, and
the mutation path (if any). It replaces the stage-1 demo-export map, whose
"exists/extend/new" claims are stale — the mission read models now exist.

Legend — **Source status**:

- `live` — derived from the real provider/store on every read.
- `seeded` — served from `SEED` in `observatory_mission_control_state.py`
  whenever the durable collection is absent; in live mode this is fabricated
  data and is an MC-01 defect.
- `partial` — some sections live, some seeded/derived.
- `demo` — read from `web/src/data/demo.ts` in the browser; no API call.
- `dead` — rendered control with no handler (button does nothing).
- `static` — hardcoded copy in the route file.

## Cross-cutting

| Concern | Current reality | Required by roadmap |
|---|---|---|
| Base paths | `/api/observatory/mission` (read models), `/api/observatory` (substrate) | unchanged |
| Environment scoping | **None.** `?environment=` is not sent; the topbar switcher is local UI state over `demo.ts` `environments` | `GET /mission/context` server identity (MC-02) |
| Data mode | None — `load_records()` silently falls back to `SEED` on absent/empty/error | `PHLO_OBSERVATORY_DATA_MODE=live\|demo`, default live (MC-01) |
| Read evidence | None — responses have no `evidence.status`/`observedAt`/`lastConfirmedAt` | shared evidence envelope per section (MC-01) |
| Errors | `ApiError` carries status + problem `detail` | 404/401/403/422/409/503 typed outcomes (MC-01) |
| Mutations | `POST /api/observatory/actions` uses `{action_id, params, expected_state, dry_run?, idempotency_key}` — **not** the `{family,target,dryRun}` shape previously documented here | shared preview/commit contract, `action_id`/`dry_run`/`idempotency_key` naming kept (MC-05) |
| Live updates | TanStack `staleTime: 15_000` + `refetchOnWindowFocus`; no polling loop, no SSE in the client | 15s active-page polling, pause hidden, back off (MC-03) |
| Read-side writes | `_state_dir()` creates `.phlo/observatory` on every `load_records` call | GETs must not create dirs or import state (MC-01) |

## Shell (`__root.tsx`, `app-shell`, topbar)

| Control | Renders from | API route | Provider/store | Status | Mutation |
|---|---|---|---|---|---|
| Alert inbox | `alerts` from `demo.ts` | `GET /mission/overview/alerts` exists but **is not called** — `queries.alerts()` is dead code | `alerts` collection → `SEED` | demo | none |
| Command palette / search | `searchEntries` from `demo.ts` | none (substrate `GET /api/observatory/search` exists, unused) | — | demo | none |
| Environment switcher | `environments` = `["Production","Staging","Development"]` | `GET /mission/overview/environments` exists but **is not called** — `queries.environments()` is dead code | `environments` collection → `SEED` | demo | none |
| Footer env/project label | `{env}` + `Retail analytics` + `Example data` chip | none | hardcoded | static | — |
| Footer freshness line | `Updated 09:35:12 UTC · Auto-refresh 15s` | none | hardcoded string | static | — |
| Nav badges/counts | `NAV_ITEMS` in `app-sidebar` | none | hardcoded counts | static | — |
| Breadcrumbs | route meta | — | — | static | — |
| Theme toggle | `ThemeProvider` local state | — | — | n/a | — |

## Overview — `/` (`index.tsx`)

| Panel | Client query | API route | Provider/store | Status |
|---|---|---|---|---|
| Summary metrics | `queries.overviewSummary()` | `GET /mission/overview/summary` | `derive_overview_summary` → live runs/services | live |
| Attention list | `queries.attention()` | `GET /mission/overview/attention` | `attention` collection → `SEED` | seeded |
| Active execution | `queries.execution()` | `GET /mission/overview/execution` | live Dagster run list | live |
| Data products | `queries.dataProducts()` | `GET /mission/overview/data-products` | live asset graph + run state | live |
| Rail — services | `queries.overviewRail()` | `GET /mission/overview/rail` | `derive_platform_services` (containers/readiness) | live |
| Rail — release queue | same | same | `overview_rail` collection → `SEED` | seeded |
| Rail — governance | same | same | `overview_rail` collection → `SEED` | seeded |
| Rail — recovery | same | same | `overview_rail` collection → `SEED` | seeded |

The rail is one endpoint serving a composite: live services joined onto a
seeded record. MC-01 must split evidence per section so a healthy services
section cannot certify seeded queue/governance/recovery rows.

## Runs — `/runs/orders-daily` (`runs/orders-daily.tsx`)

Route is hardcoded to `RUN_ID = "r7e42b"`. All queries fire against that id.

| Panel | Client query | API route | Provider/store | Status |
|---|---|---|---|---|
| Header/meta | `queries.runDetail(RUN_ID)` | `GET /mission/runs/{run_id}` | live Dagster run + evidence DB join | live |
| Details tab | `detail.details` | same | live + declared config | partial |
| Stages tab | `queries.runStages(RUN_ID)` | `GET /mission/runs/{run_id}/stages` | `run_stages` collection → `SEED` | seeded |
| Quality tab | `queries.runQuality(RUN_ID)` | `GET /mission/runs/{run_id}/quality` | `run_quality` collection → `SEED` (incl. fabricated failing-row sample) | seeded |
| Events tab | `queries.runEvents(RUN_ID)` | `GET /mission/runs/{run_id}/events` | `run_events` → `SEED` | seeded |
| Traces tab | `queries.runTraces(RUN_ID)` | `GET /mission/runs/{run_id}/traces` | `run_spans` → `SEED` (invented spans) | seeded |
| Artifacts tab | `queries.runArtifacts(RUN_ID)` | `GET /mission/runs/{run_id}/artifacts` | `run_artifacts` → `SEED` | seeded |
| Consumers tab | `queries.runConsumers` defined, **not called** | `GET /mission/runs/{run_id}/consumers` exists | `run_consumers` → `SEED` | dead+seeded |
| Configuration tab | `detail.details` (not `runConfiguration`) | `GET /mission/runs/{run_id}/configuration` exists, **not called** | `run_config` → `SEED` | dead+seeded |
| Logs tab | `queries.runLogs(RUN_ID)` | `GET /mission/runs/{run_id}/logs` | `run_logs` → `SEED` | seeded |
| "Compare with last success" | — | — | — | dead |
| "Preview retry" | — | `POST /api/observatory/runs/{id}/retry` exists (substrate) | run action contract `run.retry`, `lakehouse:operate` | dead |

## Dataset — `/datasets/orders` (`datasets/orders.tsx`)

Route ignores its own path: selects `assets[0]` and falls back to
`"marts.orders"`. No list/search entry point.

| Panel | Client query | API route | Provider/store | Status |
|---|---|---|---|---|
| Meta/metrics | `queries.datasetDetail(id)` | `GET /mission/datasets/{dataset_id}` | `derive_dataset` → live asset graph + declared metadata | live |
| Schema | `detail.schema` | same | live (catalog/capability schema) | live |
| Preview | `detail.preview` | same | live bounded read where capability exists | live |
| Checks | `detail.checks` | same | `derive_dataset_checks` → Dagster GraphQL check defs + executions | live |
| Lineage | `detail.lineage` | same | `derive_dataset_lineage` → live asset graph edges (asset-level only) | live |
| Recent runs | `detail.runs` | same | live run list filtered to asset | live |
| Ownership | `detail.ownership` | same | `owner`/`sla` asset metadata | live |
| Access | `detail.access` | same | `dataset_governance` collection → `SEED` | seeded |
| Governance detail | `queries.datasetGovernance` defined, **not called** | `GET /mission/datasets/{id}/governance` exists | `dataset_governance` → `SEED` | dead+seeded |
| "Preview materialization" | — | `POST /api/observatory/assets/{id}/materialize` exists (substrate) | asset materialize path | dead |
| "Explore data" | — | — | — | dead |
| Banner | hardcoded | — | — | static |
| Tab labels | hardcoded counts, e.g. `Schema · 8` | — | — | static |

## Releases — `/releases` (`releases.tsx`)

| Panel | Client query | API route | Provider/store | Status |
|---|---|---|---|---|
| Summary | `queries.releaseSummary()` | `GET /mission/releases/summary` | `derive_release_candidates` → live Nessie WAP branches | live |
| Pending candidates | `queries.releaseCandidates()` | `GET /mission/releases/candidates` | live WAP branch scan | live |
| Candidate detail | `queries.releaseCandidate(id)` | `GET /mission/releases/candidates/{id}` | `derive_release_candidate` → live branch + report | live |
| Completed releases | `queries.completedReleases()` | `GET /mission/releases/completed` | `derive_completed_releases` → live catalog history | live |
| Providers tab | `PROVIDER_ROWS` | — | hardcoded in route file | static |
| "Preview publication" button | — | `POST /api/observatory/actions` families `candidate:claim/review/promote/reject` exist | WAP authority + journal | dead |
| "Open dataset" link | `onOpenDataset` | navigates to hardcoded `/datasets/orders` | — | partial |

## Platform — `/platform` (`platform.tsx`)

| Panel | Client query | API route | Provider/store | Status |
|---|---|---|---|---|
| Summary | `queries.platformSummary()` | `GET /mission/platform/summary` | `derive_platform_summary` → live runtime/readiness | live |
| Services | `queries.platformServices()` | `GET /mission/platform/services` | `derive_platform_services` → live containers | live |
| Service diagnostics rail | `queries.serviceDiagnostics(unready)` | `GET /mission/platform/services/{service_id}` | live environment probe | live |
| Backup | `queries.backupCoverage()` | `GET /mission/platform/backup` | `backup`/`maintenance` collections → `SEED` | seeded |
| Maintenance | `queries.maintenance()` | `GET /mission/platform/maintenance` | `maintenance` → `SEED` | seeded |
| Banner | `Loki is running…` | — | hardcoded | static |
| "View configuration", "Run diagnostics" | — | — | — | dead |
| Probe/restart | — | `POST /api/observatory/actions` families `service:start/stop/restart/add` exist | service control machinery | dead |

## Governance — `/governance` (`governance.tsx`)

| Panel | Client query | API route | Provider/store | Status |
|---|---|---|---|---|
| Summary | `queries.governanceSummary()` | `GET /mission/governance/summary` | derived counters (ownership gaps live; rest seeded) | partial |
| Publication reviews | `queries.publicationReviews()` | `GET /mission/governance/publication-reviews` | `publication_reviews` → `SEED` — **no producer exists** | seeded |
| Access drift | `queries.accessDrift()` | `GET /mission/governance/access-drift` | `access_drift` → `SEED` — no declared→compiled→verified triad | seeded |
| Ownership gaps | `queries.ownershipGaps()` | `GET /mission/governance/ownership-gaps` | `derive_ownership_gaps` → live `owner` metadata | live |
| Audit | `queries.auditEvents()` | `GET /mission/governance/audit` | `audit` → `SEED` — real audit exists via callbacks, not surfaced here | seeded |
| Publication plan rail | `queries.publicationPlan("logistics.shipments")` — hardcoded id | `GET /mission/governance/publication-plan/{dataset_id}` | `publication_plan` → `SEED` | seeded |
| "Preview Dataset publication", "Preview grant reconciliation", "Export audit log", "Invite member", "Review access drift", "View audit log" | — | `dataset:publish`/`candidate:*` families exist; others none | — | dead |

## Settings — `/settings` (`settings.tsx`)

| Panel | Client query | API route | Provider/store | Status |
|---|---|---|---|---|
| Summary | `queries.settingsSummary()` | `GET /mission/settings/summary` | derived counters | partial |
| Provider connections | `queries.providerConnections()` | `GET /mission/settings/providers` | `provider_connections` → `SEED` | seeded |
| Provider impact | `queries.providerImpact("polaris")` — hardcoded id | `GET /mission/settings/providers/{provider_id}/impact` | `provider_impact` → `SEED` | seeded |
| Notification rules | `queries.notificationRules()` | `GET /mission/settings/notifications` | `notification_rules` → `SEED` — no delivery producer | seeded |
| Members | `queries.workspaceMembers()` | `GET /mission/settings/members` | `members` → `SEED` | seeded |
| Defaults | `queries.workspaceDefaults()` | `GET /mission/settings/defaults` | `defaults` → `SEED` | seeded |
| Banner | `Polaris is unreachable` | — | hardcoded | static |
| "View configuration", "Run diagnostics", "Invite member" | — | — | — | dead |

## Docs / Reference — `/docs`, `/reference`

No API. `/docs` renders static `documentationGroups` with an `ExampleDataChip`
and two dead buttons. `/reference` renders the CSS token layer.

## Unused-but-defined client queries

`alerts`, `environments`, `runConsumers`, `runConfiguration`,
`datasetGovernance`. The shell still reads `demo.ts` directly, so the alert and
environment mission endpoints exist with no consumer.

## Mutation surface actually available (substrate, not mission)

Declared in `security_manifest.py`; all require `lakehouse:operate` unless
noted. None is currently wired to a UI control.

| Route | Contract |
|---|---|
| `POST /api/observatory/actions` | generic `ObservatoryActionRequest{action_id,params,expected_state}`; families `dataset:*`, `candidate:*`, `service:*`, `quality:rerun`, `alert:*`, `branch:*`, `storage:*`, `metadata:*`, `api:*` |
| `POST /api/observatory/assets/{id}/materialize`, `POST .../backfill` | asset execution |
| `POST /api/observatory/runs/{id}/retry`, `POST .../cancel` | `run.retry`/`run.cancel` run-action contracts, journaled |
| `POST /api/observatory/branches`, `POST /merge`, `POST /branches/actions`, `DELETE /branches/{name}` | catalog branch ops |
| `PUT /api/observatory/dataset-workflow/config`, `PUT /settings`, `PUT /preferences` | settings writes |
| `POST /api/observatory/query`, `POST /query-with-filters`, `POST /page` | bounded query reads |

Guarded machinery already in place: scope check, rate limit, idempotency key,
operation journal, audit callback, replay reconciliation. ADR 0047 invariant: a
privileged mutation without durable audit persistence does not execute.

## Producer gaps (must stay unsupported until built)

These have **no upstream producer** — they must render explicit
unsupported/absent states in live mode, never seeds:

- publication review records (policy verdict + CAS + audit)
- access-policy declared → compiled → verified triad
- notification rule storage/delivery
- workspace member/default administration records
- release queue ordering beyond WAP branch presence
- recovery/backup evidence
- declared contract metadata beyond `owner`/`sla`/`consumers`/`quality_provider`
  (domain, classification, retention, contract version)
- row-level lineage (only asset-level graph edges exist)
- per-run traces/consumers (no producer; expose as unrecorded, never invented)

## Acceptance coverage required

Each panel needs a truthful-outcome test per MC-01/acceptance conventions:
healthy-nonempty, healthy-empty, unknown-id 404, unavailable-dependency,
stale-cache, corrupt/partial store, and (for controls) preview→confirm→reconcile
with provider evidence. The fixture for these is
`examples/lakehouses/wap-failure-lab` booted via `BundledStackHarness` — see
`docs/observatory/acceptance-runbook.md`.
