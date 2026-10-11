# Unified API remaining acceptance

This record covers the remaining work in [#1035](https://github.com/phlohouse/phlo/issues/1035), against main at [0b8685e](https://github.com/phlohouse/phlo/commit/0b8685e66cb9a299fa1a3c62adf9cacfec0505be). It does not mark the tracker complete or establish production readiness. The bundled replacement and original API phases were already delivered.

## Contracts added or corrected

| Contract | Behaviour and limit |
| --- | --- |
| `GET /api/v1/datasets?limit=...` | Loads project declarations through the existing registry loader, then projects the canonical core Dataset authority. Scope is `project`, not an environment. Unsupported filters return 400. The 1–500 bound reports truncation. Publication, workflow, and approval stay nullable when not recorded. A registry or durable-store outage returns 503, not an empty inventory. This is the declaration inventory, not a list of every stored candidate. |
| `GET /api/v1/queries/history?env=...&limit=...` | Lists completed durable workspace execution metadata for the selected environment and current mapped ref, newest first. The 1–100 bound reports truncation. Other actors' SQL, results, and identities are not exposed. Missing configuration is explicitly unavailable. Coverage is partial because attempted, failed, and cancelled queries are not this completed-execution ledger. |
| `GET /api/v1/projects/{project_id}/runs/{run_id}/attempts/{attempt}/report` | Canonical alias of the existing durable core RunReport builder. Project, logical run, and attempt are exact producer-recorded identities. Unknown identity returns 404. The client displays incomplete and unavailable evidence separately from terminal success. Scoped service tokens remain confined to their exact report. The legacy alias remains mounted. |
| Existing response schemas | Defaulted nullable fields remain nullable. Pydantic's serialization schema marks defaulted fields required only because these responses actually emit them. Existing role and run-status vocabularies are typed. Audit events retain their full additional metadata. Rollback, effects, follow-ups, staging results, and query columns now describe the emitted response. |
| Existing action variants | Dry-run results contain a skipped status and message, not an invented acceptance decision or run ID. Live provider outcomes and planned materialisation outcomes have distinct schemas. The client still requires acceptance and the appropriate live outcome before reporting success. Optional release tags remain nullable. |

The frozen prerequisite `issue-1035-contract-base` points to [a3dbd26](https://github.com/phlohouse/phlo/commit/a3dbd26d793a068c04f18a91458250fac258040f). It contains contract corrections only. Product additions follow it on the same issue branch and PR.

The additional stable prerequisite `issue-1035-text-contract-base` points to [6613371](https://github.com/phlohouse/phlo/commit/6613371d0bf55c6645bbc366162b4c54a1a2e625). It adds subscription and follow-up mutation response models, preserving the created follow-up's incident identity and nullable dates. Persisted replay dates are restored through the same wire model before serialization. A real PostgreSQL HTTP regression checks required response metadata, true and false subscriptions, nullable dates, completed timestamps, and identical replay responses. The original frozen branch is unchanged. There is no second issue PR.

## Executed evidence and outstanding acceptance

The browser used the built bundled Node application, real local FastAPI, a disposable PostgreSQL 18 database, and two real Dagster code locations. These are local acceptance sources, not production or staging deployments. No shared database migration, production write, or deployment was performed.

Stopping the disposable database produced HTTP 503 from the Dataset endpoint with `Core Dataset authority is unavailable.` The browser's shared incident bootstrap failed first with its own 503. No inventory appeared. This checks the real API outage boundary, not an isolated rendered Dataset error component. The database was restarted afterwards.

After that restart, the existing cached PostgreSQL run-evidence store returned HTTP 500 with `connection already closed`. Restarting the local API restored the same retained report and terminal outcome. Automatic store reconnection is not proved or fixed here. The new report alias uses the same existing builder and store as the legacy endpoint.

| Remaining criterion | Executed evidence | Still unproved or requiring a decision |
| --- | --- | --- |
| Governed Dataset inventory | HTTP parity test loads a real project workflow declaration and compares inventory entries with CLI core projections. Bounds and rejection of environment filters pass. Browser renders `gold.orders`, owner `analytics`, publication not recorded, and blocked quality readiness from the real authority and PostgreSQL store. | Not a candidate-store explorer or environment-specific publication model. Those are not inferred from materialisation. |
| Shared query history | Disposable PostgreSQL integration checks durable ordering, environment and ref separation, privacy, and bounds. Browser refreshes the real empty completed ledger even while the query workspace is unavailable. | No populated browser journey through real Trino execution was run. No SQL or results are synthesised to fill the gap. |
| Failure and slow-run correlation | Existing job patterns derive groups from bounded run evidence. Existing frontend common-source failure grouping remains the authority. Pattern run IDs now link to run detail. Phase-4 HTTP tests exercise scope, patterns, and mutation guards. | Populated real-provider failure/slow-run browser acceptance remains unproved. No duplicate grouping engine or speculative scheduled detector was added. |
| Detailed usage and SLA | Existing SLA/freshness calculation and detailed evidence are credited, not rebuilt. New Usage evidence tab reads the existing preview and verified Trino-input endpoints. Real browser capture shows both sources unavailable, not zero use or no adoption. Existing asset HTTP tests cover freshness and source failures. | Populated Trino event-listener and preview access browser journeys remain unproved. Missing SLA remains unknown. |
| Per-column schema decisions | Browser submits a column choice and justification to real FastAPI, then reads its durable PostgreSQL decision with actor and timestamp. Existing signed-resolution PostgreSQL tests exercise replay, stale signatures, and audit evidence. | The local form uses illustrative operator-supplied refs and hashes. Recording does not prove a valid Nessie conflict, automatic code-contract edit, or successful merge. Those end-to-end requirements remain open. |
| Dedicated durable run reports | Actual local Dagster execution succeeds. Its real provider ID and terminal outcome are retained through the durable store, then read through canonical HTTP and the browser. Success, unknown-report 404, incomplete evidence, and narrow layout were captured and inspected. Existing report tests cover attempt isolation and scoped-token confinement for both aliases. | Local execution was not a full ingestion, transform, quality, and publication journey. Its report deliberately lists missing groups. Its unscoped local Dagster record was removed from the disposable instance after retaining the outcome. |
| Browser extensions | Existing Python manifests, assets, and settings contracts remain intact. See [extension contracts](observatory-extensions.md). | No browser module loader or extension-specific CSP policy exists. A reviewed trust and module-loading policy is an owner decision, not permission to execute arbitrary packages in the user session. |
| Strong drift and browser gates | Corrected #998 guard at [6716167](https://github.com/phlohouse/phlo/commit/671616729dee629c96bc7859074d474b18c5442f) passes all 111 product-tree call variants with zero mismatches, including five JSON-text inner schemas and three raw text or no-content contracts. Four tests include method, required-field, default-annotation, JSON-text rejection, and raw response negatives. #998 independently passes 104 unchanged narrow-tree variants and 125 frontend tests. | Earlier green comparisons skipped the five JSON-text schemas and are superseded, not accepted exceptions. The controlled HTTP browser fixture is not real-provider readiness evidence. #998 owns CI integration. |
| Deployment restrictions | Existing phase-4, query, staging, and signature restrictions remain in place. | #989 shared-store guarantees are not integrated by this change. Single-replica assertions and file-backed operation/audit limitations are not cross-replica safety. |

The seven additional product-tree variants are intentional additions, not gate exclusions. All paths below are relative to the inner frontend's `src/lib/data/api` directory.

| File | Method and path |
| --- | --- |
| `assets.ts` | GET `/api/v1/assets/{asset_id}/usage`; GET `/api/v1/assets/{asset_id}/query-usage` |
| `incidents.ts` | GET and POST `/api/v1/incidents/{incident_id}/schema-decisions` |
| `query.ts` | GET `/api/v1/queries/history` |
| `datasets.ts` | GET `/api/v1/datasets` |
| `reports.ts` | GET `/api/v1/projects/{project_id}/runs/{run_id}/attempts/{attempt}/report` |

## Original phase evidence

These executed checks prove bounded contracts, not all original phase exits. Test fixtures and disposable services are distinguished from real browser sources above.

| Phase | Executed check and boundary |
| --- | --- |
| 0 | [Mounted-route inventory](../../packages/phlo-api/tests/test_v1_contract.py), generated reference parity, malformed environment and payload checks. |
| 1 | [Identity, environment, service and outage contracts](../../packages/phlo-api/tests/test_v1_api.py). Real local Dagster reads return only `warehouse/orders` in prod and only `warehouse/invoices` in staging. Both environments have distinct configured locations and refs. |
| 2 | [Disposable PostgreSQL incident replay and grouping](../../packages/phlo-api/tests/test_incidents_postgres.py), [signed resolution](../../packages/phlo-api/tests/test_incident_signed_resolution_postgres.py), and [query parity](../../packages/phlo-api/tests/test_incident_query_parity.py). |
| 3 | [Asset contracts](../../packages/phlo-api/tests/test_v1_api.py), [Dataset parity](../../packages/phlo-api/tests/test_dataset_cutover_parity.py), and real unavailable-usage browser state. |
| 4 | [Job/run patterns and guarded actions](../../packages/phlo-api/tests/test_v1_jobs_read.py), [asset operation contracts](../../packages/phlo-api/tests/test_asset_operations.py), and canonical durable report tests in [Observatory API contracts](../../packages/phlo-api/tests/test_observatory_api.py). |
| 5 | [Query API](../../packages/phlo-api/tests/test_v1_query_api.py), including a real disposable PostgreSQL history test. The full workspace still needs real Trino qualification. |
| 6 | [Audit verification and tampering](../../packages/phlo-api/tests/test_v1_admin_audit.py), [identity contracts](../../packages/phlo-api/tests/test_v1_admin_identity.py), and signed-resolution PostgreSQL tests. |
| 7 | Existing signed merge and per-column decision checks remain in [branch workflow contracts](../../packages/phlo-api/tests/test_v1_branch_workflows.py). The UI does not claim that recording a decision changes a code contract. |
| 8 | [Staging contracts](../../packages/phlo-api/tests/test_v1_staging.py) exercise stale candidates, signature binding, conflicts, distinct targets, and idempotent promotion. These are isolated Git/provider fixtures, not a deployed promotion. |
| 9 | Built frontend, frontend tests, and real local captures cover the additions listed above. Complete nine-area real-provider acceptance and browser extension loading remain open. No legacy URL was deleted. |

The focused Dataset, report, and release-consumer run passed 181 tests. The final disposable PostgreSQL selection passed four tests, covering shared history, incident mutation HTTP contracts, transaction grouping, and signed-resolution persistence. The earlier contract-correction selection passed 178 tests with six integration cases deselected.

The following commands reproduce the public-boundary checks. Integration tests require Docker and create disposable databases. They do not use a shared database.

```bash
make setup
make check
uv run --locked pytest packages/phlo-api/tests/test_dataset_cutover_parity.py packages/phlo-api/tests/test_observatory_api.py tests/scripts/test_release_golden_path.py -m 'not integration'
uv run --locked pytest packages/phlo-api/tests/test_v1_query_api.py packages/phlo-api/tests/test_incidents_postgres.py packages/phlo-api/tests/test_incident_signed_resolution_postgres.py -m integration
make docs-build
npm --prefix packages/phlo-observatory/src/phlo_observatory test
npm --prefix packages/phlo-observatory/src/phlo_observatory run build
```

The corrected comparison used #998's unchanged `client-contracts.mjs` and `contracts.test.mjs` at 6716167. The complete comparison and three negative-test groups pass. Those files were removed after validation, not copied into this PR. This supersedes the earlier response-schema claim because the earlier discovery skipped five text-fetched JSON schemas. Normal frontend tests pass 115 tests in 24 files, and `make check` passes after the audit test's formatter correction. The report's narrow viewport has one `main`, no document-level horizontal overflow at 390 pixels, and a vertically scrollable report container. A narrow multi-column decision form rejects duplicate names without a write. Its two column inputs each measure 324 pixels at a 390-pixel viewport, with no document-level horizontal overflow.

## Remaining legacy consumers

The [phase-0 route decisions](../architecture/unified-api-phase-0.md) still classify every mounted legacy method and path. The inventory below updates the actual first-party callers. Documentation examples and test fixtures are not runtime consumers. No absence of an in-repository caller authorises removal of a public capability.

All `O` paths below use the exact prefix `/api/observatory`. The caller is `packages/phlo-mcp/src/phlo_mcp/api_client.py`, exposed through MCP tools and resources in `server.py`.

| MCP method | Method and path | Decision |
| --- | --- | --- |
| `get_platform_health` | GET `/api/observability/health` | Keep aggregate observability capability. |
| `get_config`, `get_plugins` | GET `/api/config`, GET `/api/plugins` | Keep configuration and installed-plugin inventory. |
| `install_plugin` | POST `O/packages/install` | Keep distinct administrator package action. |
| `get_services`, `get_service_info` | GET `/api/services`, GET `/api/services/{service_name}` | Keep compatibility until registry detail and environment-scoped live health callers migrate. Shapes are not interchangeable. |
| `get_assets`, `get_asset_details` | GET `O/assets`, GET `O/assets/{asset_key_path}` | Migrate only with an explicit MCP environment selection and response-shape contract. Keep URLs meanwhile. |
| `list_operations`, `get_operation_context` | GET `O/operations`, GET `O/operations/{operation_id}/agent-context` | Keep distinct operation/agent-context capabilities. |
| `get_contracts`, `get_contract`, `search_contracts` | GET `/api/contracts`, GET `/api/contracts/{table_name}` | Keep declaration contract reads. Dataset readiness is not a replacement for the code contract. |
| `create_workflow`, `list_workflows` | POST and GET `/api/authoring/workflows` | Keep authoring. |
| `validate_workflow`, `validate_schema` | POST `/api/authoring/workflows/validate`, POST `/api/authoring/schemas/validate` | Keep authoring validation. |
| `list_templates`, `lint_project`, `run_doctor` | GET `/api/authoring/templates`, POST `/api/authoring/project/lint`, GET `/api/authoring/doctor` | Keep authoring templates and diagnostics. |
| `search_assets`, `search_runs` | GET `O/search`, GET `O/runs` | Keep compatibility pending explicit MCP environment and search-contract migration. |
| `get_quality_results` | GET `O/quality` | Keep aggregate quality capability; asset check reads are not a shape-compatible replacement. |
| `get_lineage`, `diff_schema` | GET `O/asset-graph/neighbors`, POST `O/schemas/diff` | Keep graph neighbourhood and run-schema comparison capabilities. |
| `get_service_status`, `get_recent_alerts`, `get_dashboard_links` | GET `/api/observability/services`, GET `/api/observability/alerts`, GET `/api/observability/dashboards` | Keep provider observability capabilities. |
| `get_run_logs`, `search_run_logs`, `follow_run_logs` | GET `/api/loki/runs/{run_id}`, GET `/api/loki/runs/{run_id}/stream` | Keep Loki query and SSE contracts. Dagster polling is not Loki log search. |
| `get_materialization_history` | GET `O/assets/{asset_key_path}/materializations` | Keep compatibility pending environment-aware migration. |
| `get_run_trace_spans`, `get_trace_spans` | GET `/api/observability/traces/runs/{run_id}`, GET `/api/observability/traces` | Keep provider trace capability. |
| `get_logs_query_link`, `get_metrics_query_link` | GET `/api/observability/links/logs`, GET `/api/observability/links/metrics` | Keep provider links. |
| `materialize_asset`, `backfill_asset` | POST `O/assets/{asset_key_path}/materialize`, POST `O/assets/{asset_key_path}/backfill` | Keep compatibility until MCP adopts environment, confirmation, plan, and idempotency contracts. |
| `retry_run`, `cancel_run` | POST `O/runs/{run_id}/retry`, POST `O/runs/{run_id}/cancel` | Keep compatibility until MCP adopts environment and expected-state contracts. |
| `list_partitions`, `get_run_status` | GET `O/assets/{asset_key_path}/partitions`, GET `O/runs/{run_id}/status` | Keep partition capability and compatibility status read. |

Other consumers and URL producers have these decisions:

| Consumer or producer | Path | Decision |
| --- | --- | --- |
| Native compose readiness, API container healthcheck, packaged profile harness | GET `/health` | Keep anonymous liveness, distinct from authenticated dependency health. |
| `packages/phlo-testing/src/phlo_testing/profile_harness.py` | GET `/api/backends` | Keep backend capability inventory. Services do not replace backend-provider information. |
| `scripts/release_golden_path.py` | GET canonical durable report | Migrated in this change before any legacy retirement. |
| `observatory_api/run_action_contract.py` | Generates canonical report URL | Migrated to `/api/v1`; not a separate request caller. |
| `observatory_api/extensions.py` | Generates `O/extensions/{name}/assets` URLs | Keep Python extension asset contract. No current bundled browser consumer was found. |
| Continuity CLI, recovery drill, and release operations | Direct Python operations/providers, not `/api/continuity` HTTP | Keep continuity routes and provider capabilities. No first-party HTTP caller was found; that does not establish that external clients are absent. |

The remaining MCP migration needs a reviewed environment-selection and tool compatibility contract. Silently choosing prod would change the client's meaning. This record preserves that unresolved decision instead of deleting URLs or claiming cutover complete.
