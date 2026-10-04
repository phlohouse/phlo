# Staging promotions

The staging promotion API is disabled unless the operator configures all of these values:

- `PHLO_STAGING_SINGLE_REPLICA=true` and `WEB_CONCURRENCY=1` assert that one API process controls promotion.
- `PHLO_PROMOTION_PROD_WORKTREE` and `PHLO_PROMOTION_STAGING_WORKTREE` identify distinct, clean, linked Git worktrees. The worktrees must share one Git object store, and the production revision must be an ancestor of the staging revision.
- `PHLO_PROMOTION_PROD_REF` and `PHLO_PROMOTION_STAGING_REF` identify the Nessie branches. Each value must match the corresponding `nessie_ref` in `PHLO_V1_ENVIRONMENTS`.
- `PHLO_PROMOTION_DAGSTER_LOCATION` identifies the production Dagster code location to reload. The value must match the production `dagster_location` in `PHLO_V1_ENVIRONMENTS`.
- `PHLO_PROMOTION_DAGSTER_CHECK_JOBS` contains exactly three distinct, comma-separated Dagster job names. The API maps them in order to the `tests`, `contracts`, and `audits` checks and launches them in the staging code location.

Every route requires exactly one `env=staging` query parameter. The API rejects `env=prod`, a missing or repeated `env`, and caller-supplied target overrides.

## Candidate and checks

`GET /api/v1/staging/promotions/candidate?env=staging` returns the pinned Git revisions, Nessie hashes, changed code paths, jobs, and table-copy inventory. The candidate ID covers that state. The API rejects dirty, unrelated, or non-fast-forward worktrees. It also rejects symlinks, Git submodules, path escapes, and paths for secrets, dependencies, or generated state.

`POST /api/v1/staging/promotions/candidate/checks?env=staging` requires `lakehouse:operate`, an `Idempotency-Key` header, and this body:

```json
{"candidate_id": "<64-character candidate ID>"}
```

The request launches the three configured jobs against the mapped staging branch. Each run is tagged with the staging environment, Nessie ref, Git revision, Nessie hash, and candidate ID. The API does not reuse an older untagged run. It rejects the result if the candidate changes while the jobs run.

For each mutating route, the idempotency record binds the key to the authenticated actor, operation, and request body. Retrying the same intent replays the stored result. Reusing the key for another actor, operation, or body returns `409`.

## Code promotion

`POST /api/v1/staging/promotions?env=staging` requires `project:write`, an `Idempotency-Key` header, and this body:

```json
{
  "candidate_id": "<64-character candidate ID>",
  "signature_id": "<signature ID>",
  "justification": "<approval justification>",
  "confirm": true
}
```

The consumed signature must approve `staging.promote` for the candidate ID, the mapped production and staging refs, the authenticated signer, and the same justification. All three candidate-tagged check runs must have succeeded.

Promotion advances the production worktree to the staging Git revision with a compare-and-swap fast-forward, updates the checked-out files, and reloads the mapped production Dagster location. It promotes code only. It does not merge or move a Nessie branch.

## Destructive Nessie resync

`POST /api/v1/staging/resync?env=staging` is a separate destructive operation. It requires `lakehouse:operate`, an `Idempotency-Key` header, and this body:

```json
{
  "expected_prod_hash": "<current production Nessie hash>",
  "expected_staging_hash": "<current staging Nessie hash>",
  "confirm": true
}
```

The operation assigns the mapped staging Nessie branch to the pinned production hash. It uses the expected staging hash as the compare-and-swap guard and returns `409` if either supplied hash is stale. It does not change Git.
