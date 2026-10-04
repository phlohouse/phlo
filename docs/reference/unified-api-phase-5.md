# Unified API phase 5: query workspace

Phase 5 adds an environment-scoped SQL workspace under `/api/v1`. It uses the operator-mapped Nessie ref and Trino catalog for each request. It does not expose Trino's native HTTP API.

The query workspace is disabled unless the API process asserts `PHLO_V1_QUERY_SINGLE_REPLICA=1`, both environment-specific preview Trino passwords and the catalog map are configured, and `PHLO_V1_PREVIEW_SERVER_LIMITS_CONFIGURED=1`. These variables are operator assertions. They do not discover deployment replicas or verify the running Trino policies. Keep the feature disabled until the installation confirms its separate identities, catalog/ref mapping, read-only access, resource-group, and session-property configuration.

Every endpoint for a selected environment requires exactly one `?env=prod` or `?env=staging`. The API resolves the selected catalog and Nessie ref from its allowlist. Prod and staging must map to distinct catalogs and distinct refs. The request cannot set a catalog, Nessie ref, Trino URL, or Trino identity.

## Catalog, ref, and engine reads

| Method and path | Behaviour |
| --- | --- |
| `GET /api/v1/query/catalog?env=...` | Returns schemas and table names from at most 100 catalog entries. `truncated` reports when more entries exist. A Trino outage returns 503 rather than an empty catalog. |
| `GET /api/v1/query/refs?env=...` | Returns the mapped Nessie ref and its catalog. |
| `GET /api/v1/query/engines?env=...` | Reports Trino as configured when the query workspace gates pass. This is configuration status, not a live health check. |

## Submit and inspect a query

`POST /api/v1/queries?env=...` accepts one Trino-dialect `SELECT`, `WITH`, `VALUES`, or other parsed read-only query in the JSON body. The body has `sql` and an optional `row_limit` from 1 to 100. The parser rejects multiple statements, non-query statements, and any table reference that names a catalog other than the selected environment's catalog. Unqualified table references use the mapped `X-Trino-Catalog`.

The API wraps the submitted query with an outer `LIMIT` of `row_limit + 1`. Trino therefore caps the result positions before sending pages to the API. The API returns at most `row_limit` rows and marks `has_more` when it receives the extra row. Trino's preview policy must also enforce the read-only identity, physical scan budget, planning time, and run time. The API rejects plaintext Trino URLs, cancels active queries when the caller cancels or when a response exceeds its limits, and caps the returned JSON and CSV at 1 MiB. Query text is limited to 64 KiB.

The submit and explain endpoints return HTTP 202 with a query ID. Poll `GET /api/v1/queries/{query_id}?env=...` for `queued`, `running`, `cancelling`, `completed`, `failed`, or `cancelled`. The query ID belongs to the authenticated actor and the selected environment. Results stay in process memory for up to one hour after completion. A process restart loses the result record. The Trino run-time limit remains the last bound for work that outlives an API process.

`POST /api/v1/queries/explain?env=...` runs Trino `EXPLAIN` on the same bounded query form. It does not run `EXPLAIN ANALYZE`.

To cancel an active query, call `POST /api/v1/queries/{query_id}/cancel?env=...`. The API records the cancellation attempt before sending Trino's cancel request. Poll the query resource until its status becomes `cancelled` or a terminal failure appears.

To export a completed result, call `GET /api/v1/queries/{query_id}/csv?env=...`. The endpoint returns UTF-8 CSV with a header row. It rejects unfinished queries with 409 and exports over 1 MiB with 413.

## Save and manage queries

| Method and path | Behaviour |
| --- | --- |
| `GET /api/v1/queries/saved?env=...` | Lists saved queries whose environment and mapped Nessie ref match the request. |
| `POST /api/v1/queries/saved?env=...` | Creates a saved query. The body also includes `env`, which must match the query parameter. The request requires an `Idempotency-Key`. |
| `PUT /api/v1/queries/saved/{query_id}?env=...` | Replaces a saved query when `expected_version` matches. A stale version returns 409. The request requires an `Idempotency-Key`. |
| `DELETE /api/v1/queries/saved/{query_id}?env=...` | Deletes a saved query when `expected_version` matches. The request requires an `Idempotency-Key`. |

Saved queries use the existing transactional Observatory settings store in a separate `v1_saved_queries` collection. Each record binds its SQL to an environment and Nessie ref. Creates, updates, and deletes run inside the store transaction and are audited. Idempotency replays return the original result. The API limits the collection to 100 records and strips secret-like metadata fields.

## Permissions and audit records

The HTTP security manifest requires `dataset.query` on the project for query submission, explain, and cancel. Catalog discovery and result reads require `catalog.read` or `dataset.read`. Saved-query changes require `object.write`. Trino uses separate `phlo_api_preview_prod` and `phlo_api_preview_staging` identities, each restricted server-side to its environment's catalog. The API also rejects explicit catalog references outside the selected environment before it submits SQL.

Each SQL attempt is recorded in `.phlo/audit/operations.jsonl` with its actor, environment/ref target, operation, outcome, and SHA-256 hash of the SQL. The log does not contain submitted SQL. Authorization denials are logged at the security boundary before the route handler runs. Saved-query mutations use the same API operation audit writer. These records are not the hash-chained compliance audit log added in phase 6, and this phase makes no regulatory compliance claim.

The optional Trino preview bundle in `packages/phlo-trino/src/phlo_trino/preview` is not activated by the Trino service definition. Its example resource-group scan quota is a quota-period limit, not a guaranteed per-query object-store scan ceiling. Verify the deployed server configuration before setting the API assertions. The API itself limits submitted SQL size, result rows, execution time, JSON bytes, and CSV bytes.

Tests use distinct prod and staging catalog/ref mappings, deny writes and cross-environment catalog references, exercise policy denials and audit records, simulate query cancellation and engine outages, and check idempotent and stale saved-query mutations. They do not connect to a live production or staging installation.
