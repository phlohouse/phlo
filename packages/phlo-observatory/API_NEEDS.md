# Mission Control — API and data requirements

Stage 1 built every screen against `web/src/data/demo.ts`. This document maps
each demo export to the endpoint that must replace it in stage 2, and records
the cross-cutting contracts (actions, live updates, errors) the UI already
assumes.

Legend: **exists** = present in `phlo-api` today; **extend** = exists but needs
more fields or filtering; **new** = must be added.

## Cross-cutting contracts

| Concern | Contract |
|---|---|
| Base path | `/api/observatory` (the Vite dev server already proxies it) |
| Environment scoping | Every read takes `?environment=production\|staging\|development`; the topbar switcher drives it |
| Mutations | `POST /actions` with `{ family, target, params, dryRun, idempotencyKey }`; `dryRun` powers every "Preview …" control |
| Live updates | `GET /events` (SSE) carrying `{ kind, payload }`; the footer promises a 15s refresh, so a poll fallback must work too |
| Degraded reads | Responses carry `{ stale: boolean, lastConfirmedAt: string }` so stale evidence can be labelled "last confirmed" |
| Errors | RFC 9457 problem+json with a human `detail`; the UI shows `detail` verbatim in banners |

Current status of the underlying platform: **exists** — `/services`,
`/operations`, `/runs`, `/assets`, `/datasets`, `/tables`, `/quality`, `/logs`,
`/branches`, `/extensions`, `/settings`, `/capabilities`, `/search`.

What is missing is a **read-model layer shaped for these nine screens**, plus
lab-domain resources (ownership, contracts, publication) that are not modelled
today.

## Shell

| Demo export | Endpoint | Status |
|---|---|---|
| `alerts` | `GET /alerts` | new |
| `searchEntries` | `GET /search?q=` | extend (add `kind`, `to`) |
| `environments` | `GET /environments` | new |

## Overview — `/`

| Demo export | Endpoint | Status |
|---|---|---|
| `summaryMetrics` | `GET /overview/summary` | new |
| `attentionItems` | `GET /overview/attention` | new |
| `activeExecution` | `GET /overview/execution` | new |
| `dataProducts` | `GET /datasets?limit=5&order=released` | extend |
| `services` | `GET /services/health` | extend (`state`) |
| `releaseQueue` | `GET /releases/queue` | new |

## Runs — `/runs/$runId`

| Demo export | Endpoint | Status |
|---|---|---|
| `runMeta` | `GET /runs/{runId}` | extend (execution/evidence/release as separate fields) |
| `runStages` | `GET /runs/{runId}/stages` | new |
| `duplicateRows` | `GET /runs/{runId}/quality` | new |
| `runEvents` | `GET /runs/{runId}/events` | new |
| `runLogLines` | `GET /runs/{runId}/logs` | extend (`/logs` filtered by run) |
| `runSpans` | `GET /runs/{runId}/traces` | new |
| `runArtifacts` | `GET /runs/{runId}/artifacts` | new |
| `runConsumers` | `GET /runs/{runId}/consumers` | new |
| `runConfig` | `GET /runs/{runId}/configuration` | new |
| — | `POST /runs/{runId}/retry` | exists (add `dry_run`) |

## Dataset — `/datasets/$datasetId`

| Demo export | Endpoint | Status |
|---|---|---|
| `datasetMeta` | `GET /datasets/{id}` | extend (freshness, snapshots, quality rollup) |
| `datasetSchema` | `GET /datasets/{id}/schema` | extend (`/tables/{id}` exposes fields) |
| `datasetPreview` | `GET /datasets/{id}/preview` | extend (`/table-preview`) |
| `datasetChecks` | `GET /datasets/{id}/quality` | extend |
| `datasetLineage` | `GET /datasets/{id}/lineage` | extend (`/asset-graph`) |
| `datasetRuns` | `GET /datasets/{id}/runs` | new |
| `datasetOwnership` | `GET /datasets/{id}/ownership` | new |
| `datasetContract` | `GET /datasets/{id}/contract` | new |
| `datasetAccess` | `GET /datasets/{id}/access` | new |

## Releases — `/releases`

| Demo export | Endpoint | Status |
|---|---|---|
| `releaseMetrics` | `GET /releases/summary` | new |
| `pendingCandidates` | `GET /releases/candidates` | new |
| `candidateDetail` | `GET /releases/candidates/{id}` | new |
| `latestReleases` | `GET /releases/completed` | new |
| — | `POST /actions` `release:preview` / `release:promote` | new |

## Platform — `/platform`

| Demo export | Endpoint | Status |
|---|---|---|
| `platformMetrics` | `GET /platform/summary` | new |
| `platformServices` | `GET /platform/services` | extend (`/services` + readiness probe) |
| `lokiDetail` | `GET /platform/services/{name}` | new |
| `dependencyPath` | `GET /platform/services/{name}/dependencies` | new |
| `backupCoverage` | `GET /platform/backup` | new |
| `maintenanceRows` | `GET /platform/maintenance` | new |
| — | `POST /platform/services/{name}/probe` | new |

## Governance — `/governance`

| Demo export | Endpoint | Status |
|---|---|---|
| `governanceMetrics` | `GET /governance/summary` | new |
| `publicationReviews` | `GET /governance/publication-reviews` | new |
| `accessDrift` | `GET /governance/access-drift` | new |
| `ownershipGaps` | `GET /governance/ownership-gaps` | new |
| `publishShipments` | `GET /governance/publication-plan/{datasetId}` | new |
| `auditActivity` | `GET /governance/audit` | new |
| — | `POST /actions` `dataset:publish` (preview-first, CAS on state version) | new |

## Settings — `/settings`

| Demo export | Endpoint | Status |
|---|---|---|
| `settingsMetrics` | `GET /settings/summary` | new |
| `providerConnections` | `GET /settings/providers` | new |
| notification rules | `GET /settings/notifications` | new |
| members | `GET /settings/members` | new |
| defaults | `GET /settings/defaults` | new |
| — | `POST /settings/providers/{name}/test` | new |

## Documentation / Reference — `/docs`, `/reference`

No API. Documentation content lives in `web/src/content/documentation.ts`;
Reference reads the live CSS token layer.

## Data model gaps that block stage 2

1. **Ownership and contracts are not modelled.** There is no owner, domain,
   classification, freshness target, retention or contract version anywhere in
   the current API. Governance, Dataset and the Overview rail all depend on it.
2. **Publication is not a first-class transition.** Today `publication_state`
   exists on datasets, but there is no review record, no policy verdict, no
   state-version CAS, and no audit entry for a publication.
3. **Access policy has no declared/compiled/verified triad.** Drift detection
   needs all three to be queryable per dataset and role.
4. **Evidence is not retrievable per run.** Stage views, artifacts, traces and
   the quality-failure sample must be addressable by `runId`.
5. **Provider reachability is not separated from service health.** "Running but
   not ready" and per-provider dependency paths need to come from the probe
   layer, not the container list.
6. **Snapshot lineage is opaque.** `candidate` vs `released` snapshots and the
   release record that binds them must be explicit, since three screens render
   the distinction.
