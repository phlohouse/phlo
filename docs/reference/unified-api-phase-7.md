# Unified API phase 7: branch workflows

Phase 7 adds environment-scoped Nessie refs and branch operations to `/api/v1`. These routes are additive; the existing Observatory and provider routes remain mounted for current callers.

Nessie reports Iceberg conflicts at table-key granularity. Phlo records immutable, incident-linked per-column source/target choices and applies them as client-supplied Nessie `resolvedContent`, then verifies the resulting table content before reporting success. Resolution is intentionally limited to top-level primitive columns where both refs share the same table content ID and current data snapshot; nested columns and divergent data snapshots fail closed. This prevents a schema resolution from silently choosing one branch's divergent data state.

## Read refs and branch history

Every route requires exactly one `env=prod|staging` selector. The API resolves the environment through `PHLO_V1_ENVIRONMENTS`; it never accepts a Dagster location or a default Nessie ref from the caller. It exposes only that environment's configured ref and refs named with the selected environment prefix (`prod-` or `staging-`).

| Method and path | Behaviour |
| --- | --- |
| `GET /api/v1/branches?env=...` | Lists branch refs in the selected environment. |
| `GET /api/v1/branches/refs?env=...` | Lists branches and tags in the selected environment. |
| `GET /api/v1/branches/{branch_name}?env=...` | Returns one branch and its current hash. |
| `GET /api/v1/branches/{branch_name}/commits?env=...&limit=...&after=...` | Returns a bounded page of history. The opaque cursor is bound to the environment, branch, and observed head; a changed head invalidates it. At most 2,000 commits are scanned. |
| `GET /api/v1/branches/{branch_name}/diff?env=...&target=...` | Returns at most 500 Nessie content-key changes and reports whether the result was truncated. |
| `GET /api/v1/branches/{branch_name}/compare?env=...&target=...` | Returns the nearest common commit and ahead/behind counts when both histories share an ancestor within the scan limit. Otherwise, counts are `null` and `status` is `unavailable`. |

Nessie outages return an unavailable error; they are not represented as empty ref lists. A reference from the other environment is hidden as not found.

## Guarded branch operations

All branch mutations require `lakehouse:operate`, a non-blank `Idempotency-Key`, and `PHLO_V1_ACTIONS_REF_TAG_CONTRACT=1`. Shared PostgreSQL operation controls coordinate claims and resource exclusion across replicas. Without shared controls, `PHLO_V1_ACTIONS_SINGLE_REPLICA=1` and `PHLO_V1_ACTIONS_SINGLE_PROCESS=1` remain required operator assertions. The [production guide](../guides/run-in-production.md#configure-api-operation-controls-before-adding-workers) defines the shared-store deployment and migration requirements. In both modes, mapped Dagster jobs must honour their environment/ref tags, and signatures and provider hash preconditions remain mandatory.

Branch names created through this API must start with the selected environment prefix. The configured environment ref cannot be deleted; signed, hash-pinned merges may target that ref after checks pass.

| Method and path | Behaviour |
| --- | --- |
| `POST /api/v1/branches?env=...` | Creates a branch from the mapped ref or a caller-selected ref in the same environment. The request body has `name` and optional `from_ref`. |
| `DELETE /api/v1/branches/{branch_name}?env=...` | Deletes a branch only when the body `expected_hash` matches its current head; Nessie also receives that hash as a conditional delete. |
| `POST /api/v1/branches/{branch_name}/rebase?env=...` | Rebases the source branch onto a selected ref. The request supplies both expected hashes. Nessie v2 dry-runs and applies the ordered transplant against a temporary ref, then Phlo conditionally assigns the source branch and removes the temporary ref. A stale head or conflict prevents reassignment. |
| `POST /api/v1/branches/{branch_name}/checks?env=...` | Launches configured test, contract, and audit jobs against the exact branch/hash and reports their terminal run IDs and status. Requires the expected branch hash and an idempotency key. |
| `GET /api/v1/incidents/{incident_id}/schema-decisions?env=...` | Lists durable decisions for one incident in the selected environment. |
| `POST /api/v1/incidents/{incident_id}/schema-decisions?env=...` | Stores one immutable, idempotent table decision bound to both refs and hashes, with an explicit `source` or `target` selection for each differing top-level column. |
| `POST /api/v1/branches/{branch_name}/trial-merge?env=...` | Runs checks and a non-mutating Nessie merge trial. For conflicts, provide `incident_id` to load the exact-bound decisions and test a per-key resolved-content merge. The response includes `signature_target_version` for signing that exact resolution. |
| `POST /api/v1/branches/{branch_name}/merge?env=...` | Re-runs checks and the resolved trial, then consumes a single-use step-up signature bound to actor, action `branch.merge`, environment/source/target, both observed hashes, the exact schema-decision set, and the request message before applying the hash-pinned merge. The response includes the resulting ref hash and run IDs. |

Configure `PHLO_V1_BRANCH_CHECK_JOBS` as a JSON object with distinct job names in each environment. Each configured job must be a Dagster job in that environment's mapped location and must perform its named test, contract, or audit check. A missing configuration, job, run, or matching ref/hash tag is not a pass and blocks merge.

```json
{
  "prod": {
    "tests": "phlo_branch_tests",
    "contracts": "phlo_branch_contracts",
    "audits": "phlo_branch_audits"
  },
  "staging": {
    "tests": "phlo_branch_tests",
    "contracts": "phlo_branch_contracts",
    "audits": "phlo_branch_audits"
  }
}
```

Create a schema decision with `POST /api/v1/incidents/{incident_id}/schema-decisions`, binding the request to the observed refs and hashes. Each column choice is `source` or `target`; choices must exactly cover differing fields. During resolution Phlo creates isolated temporary refs at the observed source and target commits, builds the selected schema on the target snapshot, supplies Nessie's `expectedTargetContent` and `resolvedContent`, and deletes its temporary refs. A stale head, content-ID mismatch, differing data snapshot, invalid decision, failed trial, or failure to verify the final table content blocks the merge.

Create a signature with `POST /api/v1/signatures`, using `action="branch.merge"`, `target_type="branch"`, `target_id="<env>:<source>-><target>"`, the exact `signature_target_version` returned by the trial-merge response, and a justification exactly matching the merge message. For merges without schema decisions this version is `<source_hash>:<target_hash>`; with decisions it includes a SHA-256 binding to their IDs and choices. The signature endpoint still requires the configured step-up authentication provider. Missing, stale, reused, or mismatched signatures block the merge.

The API records branch operations and their outcomes in the existing operation audit log and the hash-chained audit trail. A successful merge includes its resulting Nessie ref hash. A failed audit after a provider mutation is surfaced as an unknown idempotent outcome; retrying with the same key does not repeat the provider call.

## Verification boundary

Automated API tests use separate prod and staging refs and exercise cross-environment denials, stale hashes/cursors, provider outages, hash-pinned lifecycle operations, check-run tag verification, and signed merge binding. They use mocked Dagster and Nessie responses; they do not establish compatibility with the deployed service versions or behaviour on production/staging data. No live installation is read or written by these tests.

The conservative schema-resolution limits are deliberate: this API does not merge divergent data snapshots, nested Iceberg fields, or independent content IDs. Endpoint coverage alone is not evidence of regulatory compliance.
