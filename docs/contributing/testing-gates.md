# Test and contract gates

## Coverage and required lanes

`make test-coverage` runs the complete non-integration workspace with branch
coverage, then appends the required PostgreSQL operation-control suite before
checking floors. Docker is required: the suite provisions disposable PostgreSQL
and rejects skips through `scripts.ci_required`. Its public lifecycle tests cover
shared claims, replay, evidence resolution, namespace isolation, advisory locks,
and bounded rate storage. It measures `src/phlo`, `scripts`, and every
`packages/*/src` tree.
`scripts/coverage_report.py` publishes separate line and branch percentages and
checks each against `tests/coverage-floors.json`. A new source tree without a
measurement or floor fails. The existing package-specific coverage collection
remains in CI.

CI measures the same workspace with four module-preserving shards and runs the
required PostgreSQL coverage suite in a separate job. Every producer records
branch coverage for the full source scope. The aggregate requires all four shards
and the PostgreSQL producer, verifies their successful test reports and branch
data, then combines them before enforcing the same floors. Missing producers,
failed or empty test reports, and PostgreSQL skips fail the gate.

After `make setup`, reproduce the CI producers from the repository root:

```bash
make test-coverage-workspace COVERAGE_SHARD_INDEX=0 COVERAGE_SHARD_COUNT=4
make test-coverage-postgres
```

Run the workspace command for indices `0`, `1`, `2` and `3`; each writes its own
directory beneath `test-results/coverage-inputs/`. The PostgreSQL command requires
Docker. Once every producer succeeds, combine their results:

```bash
make test-coverage-combine COVERAGE_SHARD_COUNT=4
```

The complete `coverage.json`, `totals.json` and per-producer test reports are
written to `test-results/coverage/`. `make test-coverage` remains the sequential
local entry point for the complete measurement and coverage floors.

The floors use a fresh workspace measurement, not the historical September
aggregate. They are rounded down to one decimal place to avoid display-rounding
failures. The JSON artifact contains the unrounded measurements. A floor is a
minimum, not a claim that uncovered code is safe.

Quality benchmark tests run every worker with real Parquet and DuckDB data and
check emitted counts, late failure positions, merge-key checksums, and module
provenance. They include a partial final file and a singleton final delete batch.
The comparison test launches real before/after worker processes. These are
correctness checks, not speed or memory thresholds.

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

### Parallel integration contracts

CI runs the storage contracts and three API groups on separate runners. The API
partition keeps module fixtures together and includes newly added API integration
tests in the `api-contracts` group automatically. All groups retain the required
plugin, so skips, expected failures and empty selections fail their job.

| Group | Required selection |
| --- | --- |
| `storage` | Iceberg, Dagster, MinIO migration, dbt quality, schema generation, schema conversion and ingestion |
| `api-contracts` | Integration tests under `packages/phlo-api/tests`, except the separately assigned Trino journey module |
| `api-query-journey` | The complete dataset journey and Trino integration modules |
| `api-branch-journey` | The complete signed branch journey module |
| `all` | The complete original selection; this remains the default local command |

After `make setup`, with Docker running, build the checked-in MinIO image once
and run a group from the repository root:

```bash
docker build --tag ghcr.io/phlohouse/phlo-minio:0.17.1 packages/phlo-minio/src/phlo_minio
uv run --locked python scripts/run_integration.py --group api-query-journey
```

Omit `--group` to run every original named contract. Each invocation writes
distinct JUnit reports to `test-results/integration/` and attempts every selected
suite even if an earlier suite fails. Only a selection containing Iceberg starts
the runner's shared MinIO/Nessie pair; the API journey fixtures own their services.

Each pytest process prints its 20 slowest setup, call and teardown phases taking
at least one second. Use those durations to distinguish service provisioning from
test execution. CI combines the producer reports into `integration-results` after
requiring the image build, storage contracts, every API group and the independent
PostgreSQL/quickstart/recovery job to succeed. The quickstart coverage artifact
continues to feed `pr / required`.

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

The current 66 production handlers all belong to that API-adapter category.
There are no UI-only server handlers or approved duplicate backend handlers.
Each named handler below stays in its API adapter module. Legacy consumer
migration and URL retirement belong to #1035, not this guard.

| Adapter file | Count | Handlers retained as authenticated API adapters |
|---|---:|---|
| admin.ts | 7 | `getAdminIdentity`, `changeMemberRoles`, `createInvitation`, `createServiceAccount`, `revokeServiceAccount`, `getAuditLog`, `exportAuditLog` |
| assets.ts | 12 | `getAssetPreview`, `rollbackAssetSnapshot`, `getAssetList`, `getAssetDetail`, `startAssetExactRowCount`, `getMaterializationEstimate`, `materializeAsset`, `backfillAsset`, `createAuditProposal`, `getAuditProposal`, `testAuditProposal`, `publishAuditProposal` |
| branches.ts | 9 | `getBranchesPage`, `getWapRuns`, `getBranchDetail`, `createBranch`, `deleteBranch`, `runBranchChecks`, `rebaseBranch`, `trialMerge`, `signAndMerge` |
| core.ts | 2 | `getShell`, `getOverview` |
| datasets.ts | 1 | `getDatasetInventory` |
| incidents.ts | 10 | `getSchemaDecisions`, `recordSchemaDecision`, `getIncidentList`, `getIncidentDetail`, `createIncident`, `updateIncident`, `setIncidentSubscription`, `createFollowUp`, `updateFollowUp`, `resolveIncident` |
| pipelines.ts | 7 | `getPipelineList`, `getPipelineJob`, `getRunLogPage`, `launchJob`, `changeSchedule`, `cancelRun`, `retryRun` |
| query.ts | 11 | `getQueryHistory`, `getQueryWorkspace`, `submitQuery`, `explainQuery`, `getQuerySession`, `getQueryIncidentTargets`, `pinQueryToIncident`, `cancelQuery`, `downloadQueryCsv`, `saveQuery`, `deleteSavedQuery` |
| reports.ts | 1 | `getRunReport` |
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
