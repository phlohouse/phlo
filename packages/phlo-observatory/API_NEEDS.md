# Mission Control — API and data requirements

Stage 1 renders every screen from `web/src/data/demo.ts`. This document maps each
export to the endpoint that must replace it in stage 2.

The "exists" column was verified by reading the route decorators under
`packages/phlo-api/src/phlo_api/observatory_api/`, not from memory.

Legend: **exists** = route is present today; **extend** = exists but needs more
fields or filtering; **new** = must be added.

## Cross-cutting contracts

| Concern | Contract |
|---|---|
| Base path | `/api/observatory` (the Vite dev server already proxies it) |
| Environment scoping | Every read takes `?environment=production\|staging\|development`; the topbar switcher drives it |
| Mutations | `POST /actions` with `{ family, target, params, dryRun, idempotencyKey }`. This route **exists**; the UI's "Preview …" controls need `dryRun` |
| Live updates | `GET /runs/{run_id}/stream` exists for run logs. Overview and Platform need an equivalent `GET /events` (SSE), with polling as fallback — the footer promises a 15s refresh |
| Degraded reads | Responses carry `{ stale, lastConfirmedAt }` so evidence can be labelled "last confirmed". No existing convention; must be introduced |
| Errors | RFC 9457 problem+json with a human `detail`, surfaced verbatim in banners |

## Verified existing surface

These paths are present today and are the substrate stage 2 builds on:

```
overview        /overview
capabilities    /capabilities  /capability-inventory  /surface-capabilities  /compatibility
search          /search
services        /services  /services/{id}  /health  /connection
operations      /operations  /operations/{id}  /operations/{id}/agent-context
runs            /runs  /runs/{id}  /runs/{id}/status  /runs/{id}/stream
                POST /runs/{id}/retry  POST /runs/{id}/cancel
datasets        /datasets/facets  /datasets/publishing-readiness  /datasets/{id}
assets          /assets  /assets/{id}  /assets/{id}/materializations  /assets/{id}/partitions
                /assets/{key}/checks  POST /assets/{id}/materialize  POST /assets/{id}/backfill
asset graph     /asset-graph  /asset-graph/neighbors  /asset-graph/impact  /graph*
tables          /tables  /tables/{t}/schema  /tables/{t}/metadata  /tables/{t}/row-count
                /preview/{t}  /table-preview/{id}  POST /query  POST /query-with-filters  POST /page
rows            /row-journey/{table}/{row}  /rows/{row}  /rows/{row}/ancestors  /rows/{row}/descendants
saved queries   /saved-queries  POST /saved-queries
quality         /quality  /quality/{id}  /failing
logs            /logs  /logs/facets
branches        /branches  /branches/{name}  /branches/{name}/entries  /branches/{name}/history
                POST /branches  POST /merge  POST /branches/actions  DELETE /branches/{name}
governance      /governance
extensions      /extensions  /extensions/{id}  /extension-manifests
                GET/PUT /extensions/{name}/settings  /extensions/{name}/assets/{path}
settings        /settings  GET/PUT /preferences  /dataset-workflow/config
surfaces        /pipelines  /storage  /observability  /apis  /bi
actions         POST /actions  POST /workflow-wizard/proposals  POST /workflow-wizard/actions
                POST /packages/install  POST /schemas/diff  /diff/{from}/{to}  /stage-diff
```

What is absent is a **read-model layer shaped for these nine screens**, plus the
governance resources (ownership, contracts, publication, access policy) that are
not modelled at all.

## Shell

| Export | Endpoint | Status |
|---|---|---|
| `alerts` | `GET /alerts` | new |
| `searchEntries` | `GET /search` | extend (add `kind`, `to` for deep links) |
| `environments` | `GET /environments` | new |

## Overview — `/`

| Export | Endpoint | Status |
|---|---|---|
| `summaryMetrics` | `GET /overview` | extend (counters exist; add `hint` breakdowns) |
| `attentionItems` | `GET /overview/attention` | new |
| `activeExecution` | `GET /overview/execution` | new (or derive from `/runs?state=active`) |
| `dataProducts` | `GET /datasets?limit=5&order=released` | extend (`/datasets` exists, add ordering) |
| `services` | `GET /services` | extend (add `state` readiness) |
| `releaseQueue` | `GET /releases/queue` | new |
| `governanceOverview` | `GET /governance` | extend (add ownership/policy/review counters) |
| `recoveryOverview` | `GET /platform/recovery` | new |

## Runs — `/runs/$runId`

| Export | Endpoint | Status |
|---|---|---|
| `runMeta` | `GET /runs/{runId}` | extend (split execution / evidence / release state) |
| `runDetails` | `GET /runs/{runId}` | extend (add asset, orchestrator, snapshots, branch) |
| `runStages` | `GET /runs/{runId}/stages` | new |
| `duplicateRows` | `GET /runs/{runId}/quality` | new (relates to `/quality`, but needs the failing sample) |
| `runEvents` | `GET /runs/{runId}/events` | new |
| `runLogLines` | `GET /logs?runId=` | extend (`/logs` exists, add run filter) |
| `runSpans` | `GET /runs/{runId}/traces` | new |
| `runArtifacts` | `GET /runs/{runId}/artifacts` | new |
| `runConsumers` | `GET /runs/{runId}/consumers` | new |
| `runConfig` | `GET /runs/{runId}/configuration` | new |
| — | `POST /runs/{runId}/retry` | exists (add `dry_run` for "Preview retry") |

## Dataset — `/datasets/$datasetId`

| Export | Endpoint | Status |
|---|---|---|
| `datasetMeta` | `GET /datasets/{id}` | extend (freshness, snapshots, quality rollup) |
| `datasetSchema` | `GET /tables/{t}/schema` | exists |
| `datasetPreview` | `GET /table-preview/{id}` | exists |
| `datasetChecks` | `GET /assets/{key}/checks` | exists |
| `datasetLineage` | `GET /asset-graph` | extend (record-level edges, not just asset-level) |
| `datasetRuns` | `GET /datasets/{id}/runs` | new |
| `datasetOwnership` | `GET /datasets/{id}/ownership` | new |
| `datasetContract` | `GET /datasets/{id}/contract` | new (only `publishing-readiness` exists today) |
| `datasetAccess` | `GET /datasets/{id}/access` | new |

## Releases — `/releases`

Nothing exists; the whole surface is new.

| Export | Endpoint | Status |
|---|---|---|
| `releaseMetrics` | `GET /releases/summary` | new |
| `pendingCandidates` | `GET /releases/candidates` | new |
| `candidateDetail` | `GET /releases/candidates/{id}` | new |
| `latestReleases` | `GET /releases/completed` | new |
| — | `POST /actions` `release:preview` / `release:promote` | new |

## Platform — `/platform`

| Export | Endpoint | Status |
|---|---|---|
| `platformMetrics` | `GET /platform/summary` | new |
| `platformServices` | `GET /services` | extend (readiness probe, runtime source) |
| `lokiDetail` | `GET /services/{id}` | extend (probe history, failure timestamps) |
| `dependencyPath` | `GET /services/{id}/dependencies` | new |
| `backupCoverage` | `GET /platform/backup` | new |
| `maintenanceRows` | `GET /platform/maintenance` | new |
| — | `POST /services/{id}/probe` | new |

## Governance — `/governance`

| Export | Endpoint | Status |
|---|---|---|
| `governanceMetrics` | `GET /governance` | extend (counters for ownership, classification, contracts, drift) |
| `publicationReviews` | `GET /governance/publication-reviews` | new |
| `accessDrift` | `GET /governance/access-drift` | new |
| `ownershipGaps` | `GET /governance/ownership-gaps` | new |
| `publishShipments` | `GET /governance/publication-plan/{datasetId}` | new |
| `auditActivity` | `GET /governance/audit` | new |
| — | `POST /actions` `dataset:publish` | new (preview-first, CAS on state version) |

## Settings — `/settings`

| Export | Endpoint | Status |
|---|---|---|
| `settingsMetrics` | `GET /settings` | extend (add provider/notification/member counters) |
| `providerConnections` | `GET /settings/providers` | new |
| `degradedList` | `GET /settings/providers/{name}/impact` | new |
| `unaffectedList` | `GET /settings/providers/{name}/impact` | new |
| notification rules | `GET /settings/notifications` | new (`/preferences` covers personal, not workspace rules) |
| members | `GET /settings/members` | new |
| defaults | `GET /settings/defaults` | new |
| — | `POST /settings/providers/{name}/test` | new |

## Documentation / Reference — `/docs`, `/reference`

No API. Documentation content is static in `web/src/content/documentation.ts`.
Reference reads the CSS token layer at runtime.

## Data model gaps that block stage 2

1. **Ownership and contracts are not modelled.** No owner, domain,
   classification, freshness target, retention or contract version exists in the
   API. Dataset, Governance and the Overview rail all depend on it.
2. **Publication is not a transition.** `publication_state` exists on datasets,
   but there is no review record, no policy verdict, no state-version CAS and no
   audit entry for a publication.
3. **Access policy has no declared → compiled → verified triad.** Drift detection
   needs all three queryable per dataset and role.
4. **Evidence is not retrievable per run.** Stages, traces, artifacts and the
   failing-row sample must be addressable by `runId`. Only `/runs/{id}/status`
   and `/runs/{id}/stream` exist today.
5. **Provider reachability is fused with service health.** "Running but not
   ready" and per-provider dependency paths need the probe layer to be separate
   from the container list.
6. **Snapshot lineage is opaque.** Candidate versus released snapshots, and the
   release record binding them, must be explicit — three screens render the
   distinction.
7. **No degraded-read convention.** Stale evidence is a first-class UI state
   ("last confirmed 09:21") with no server-side representation.

## Coverage check

Every export in `demo.ts` appears above. Verified by enumerating the module:

```
shell       environments · alerts · searchEntries
overview    summaryMetrics · attentionItems · activeExecution · dataProducts ·
            services · releaseQueue · governanceOverview · recoveryOverview
runs        runMeta · runDetails · runStages · duplicateRows · runEvents ·
            runLogLines · runSpans · runArtifacts · runConsumers · runConfig
dataset     datasetMeta · datasetSchema · datasetPreview · datasetChecks ·
            datasetLineage · datasetRuns · datasetOwnership · datasetContract ·
            datasetAccess
releases    releaseMetrics · pendingCandidates · candidateDetail · latestReleases
platform    platformMetrics · platformServices · lokiDetail · dependencyPath ·
            backupCoverage · maintenanceRows
governance  governanceMetrics · publicationReviews · accessDrift · ownershipGaps ·
            publishShipments · auditActivity
settings    settingsMetrics · providerConnections · degradedList · unaffectedList
```
