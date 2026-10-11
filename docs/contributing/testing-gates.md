# Test and contract gates

## Coverage and required lanes

`make test-coverage` runs the complete non-integration workspace with branch
coverage. It measures `src/phlo`, `scripts`, and every `packages/*/src` tree.
`scripts/coverage_report.py` publishes separate line and branch percentages and
checks each against `tests/coverage-floors.json`. A new source tree without a
measurement or floor fails. The existing package-specific coverage collection
remains in CI.

The floors use a fresh workspace measurement, not the historical September
aggregate. They are rounded down to one decimal place to avoid display-rounding
failures. The JSON artifact contains the unrounded measurements. A floor is a
minimum, not a claim that uncovered code is safe.

`scripts/test_summary.py` publishes executed, skipped, failed, and errored test
counts from JUnit reports, together with skip and xfail reasons. Ordinary
non-integration runs can contain optional skips. Named required lanes cannot:
the existing `scripts.ci_required` plugin rejects skips, xfails, and empty
execution. `test_missing_minio_cannot_pass_required_lane` runs a real MinIO
integration selection against a reserved, non-listening port and verifies both
the failing exit status and the published skip reason.

The required contracts in `scripts/run_integration.py` are Iceberg, Dagster,
API integration, MinIO migration, dbt quality, schema generation, schema
conversion, and ingestion. They do not claim complete external-service coverage.

The [fix regression-test-or-justification item proposed in PR1109](https://github.com/phlohouse/phlo/blob/ac75026a89985d009bcb3430830e1b892af67fa4/.github/PULL_REQUEST_TEMPLATE.md#L18-L20)
belongs to the single PR template. This change does not add a competing template
or alter repository settings.

## Surviving clock, oracle, and taxonomy findings

The three cited idempotency tests now synchronise with entered and release
events. Asset metadata deadline tests block a worker on an event instead of
guessing when a sleep finishes. Query and service timeout tests await a pending
future that the production deadline cancels. The Loki heartbeat test waits for
the fetch to start instead of a scheduling delay.

Remaining positive sleeps in API tests poll actual query-worker completion,
Dagster subprocess execution, or a disposable service. Root lifecycle tests
wait for real child-process exits and signal handling. Zero-duration async
sleeps yield control or construct an awaitable fake and do not delay a test.
Those external boundaries remain clock-driven. The inventory command is
`rg 'sleep\(' packages/phlo-api/tests tests`.

The MinIO suite no longer tests calls made only to its own `MagicMock`, or
literal policy dictionaries that never reach production. Production contributor
and migration tests still exercise configuration, credentials, and storage
operations. Real-service tests remain integration-marked.

Collection rejects tests under an `integration` directory without an integration
marker. Restore reconciliation and supported-version upgrade tests use injected
in-process providers, so they live under `tests/operations`, not a service lane.
`test_integration_directory_requires_marker` proves both rejected and accepted
collection.

## Built browser and schema checks

From `packages/phlo-observatory/src/phlo_observatory`, `npm run build` followed
by `node scripts/browser-smoke.mjs` starts the bundled Node server and a
controlled HTTP API. The smoke navigates the real application, executes SQL
through CodeMirror, checks results, and verifies permission denial removes stale
results. The HTTP fixture verifies the caller bearer and staging environment.
CI uploads screenshots and sanitised request evidence. This is not live
FastAPI, Dagster, PostgreSQL, or Trino acceptance.

The existing rendered accessibility and keyboard lineage tests remain the
accessibility checks. `route-behaviour.test.tsx` adds pending mutation, completed
result, permission failure, empty result, and registered loading/error boundaries.
The browser covers actual navigation and the real editor.

`contracts.test.mjs` generates OpenAPI from the installed FastAPI application.
`scripts/client-contracts.mjs` discovers actual production `phloApi` calls and
evaluates their actual Zod response parsers. It compares HTTP methods and response
types, required fields, unions, enums, and nested arrays/objects. JSON-text calls
use their production inner schemas after verifying the real `jsonText` helper
decodes JSON and rejects parse failures. Tests also execute that production
transform. CSV and NDJSON require string response schemas; no-content responses
must have no body. An OpenAPI default annotation does not make a field required.
Negative tests remove GET, rename required fields in ordinary and JSON-text
responses, and break text or no-content declarations. URL-only checks and
duplicated hand-written fixture schemas are not the authority. Untyped API
responses remain failures until API and product owners align their contracts.

## Retained Node runtime and handler ownership

The owner-approved #998 scope retains the authenticated TanStack Start Node
runtime. ADR 0032's historical pure-SPA target does not require deleting working
identity forwarding. Phlo API owns authorisation, provider access, queries,
governance, and backend state. Node handlers validate inputs, forward the user's
identity, parse responses, and assemble presentation data only.

The current 61 production handlers all belong to that API-adapter category.
There are no UI-only server handlers or approved duplicate backend handlers.
Each named handler below stays in its API adapter module. Legacy consumer
migration and URL retirement belong to #1035, not this guard.

| Adapter file | Count | Handlers retained as authenticated API adapters |
|---|---:|---|
| admin.ts | 7 | `getAdminIdentity`, `changeMemberRoles`, `createInvitation`, `createServiceAccount`, `revokeServiceAccount`, `getAuditLog`, `exportAuditLog` |
| assets.ts | 12 | `getAssetPreview`, `rollbackAssetSnapshot`, `getAssetList`, `getAssetDetail`, `startAssetExactRowCount`, `getMaterializationEstimate`, `materializeAsset`, `backfillAsset`, `createAuditProposal`, `getAuditProposal`, `testAuditProposal`, `publishAuditProposal` |
| branches.ts | 9 | `getBranchesPage`, `getWapRuns`, `getBranchDetail`, `createBranch`, `deleteBranch`, `runBranchChecks`, `rebaseBranch`, `trialMerge`, `signAndMerge` |
| core.ts | 2 | `getShell`, `getOverview` |
| incidents.ts | 8 | `getIncidentList`, `getIncidentDetail`, `createIncident`, `updateIncident`, `setIncidentSubscription`, `createFollowUp`, `updateFollowUp`, `resolveIncident` |
| pipelines.ts | 7 | `getPipelineList`, `getPipelineJob`, `getRunLogPage`, `launchJob`, `changeSchedule`, `cancelRun`, `retryRun` |
| query.ts | 10 | `getQueryWorkspace`, `submitQuery`, `explainQuery`, `getQuerySession`, `getQueryIncidentTargets`, `pinQueryToIncident`, `cancelQuery`, `downloadQueryCsv`, `saveQuery`, `deleteSavedQuery` |
| settings.ts | 2 | `getSettings`, `saveSettings` |
| staging.ts | 4 | `getStagingOverview`, `runStagingChecks`, `resyncStaging`, `promoteStaging` |

`scripts/adapter-boundary.mjs` inspects production TypeScript with the TypeScript
AST. It rejects provider/database dependencies, I/O outside the authenticated
transport, shared backend credentials/configuration, SQL in adapters,
noncanonical endpoints, and server functions outside the API adapter directory.
Negative tests insert prohibited calls. The guard constrains code boundaries;
it does not prove arbitrary new algorithms have no backend semantics.

`forwarding.test.ts` uses a real HTTP listener to prove caller bearer precedence,
trusted forwarded bearer fallback, absence of a configured shared-token bypass,
method/body/environment preservation, HTTP failures, unavailable API failures,
and rejection of cross-environment responses.
