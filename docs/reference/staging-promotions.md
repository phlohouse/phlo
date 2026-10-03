# Staging promotions

Code promotion and data resynchronisation require the API settings below. Candidate-check execution additionally requires three configured, executable Dagster jobs in the staging code location:

- `PHLO_STAGING_SINGLE_REPLICA=true` and `WEB_CONCURRENCY=1` assert that one API process controls promotion.
- `PHLO_PROMOTION_PROD_WORKTREE` and `PHLO_PROMOTION_STAGING_WORKTREE` identify distinct, clean, linked Git worktrees. The worktrees must share one Git object store, and the production revision must be an ancestor of the staging revision.
- `PHLO_PROMOTION_PROD_REF` and `PHLO_PROMOTION_STAGING_REF` identify the Nessie branches. Each value must match the corresponding `nessie_ref` in `PHLO_V1_ENVIRONMENTS`.
- `PHLO_PROMOTION_DAGSTER_LOCATION` identifies the production Dagster code location to reload. The value must match the production `dagster_location` in `PHLO_V1_ENVIRONMENTS`.
- For candidate checks, `PHLO_PROMOTION_DAGSTER_CHECK_JOBS` must contain exactly three distinct, comma-separated Dagster job names in `tests,contracts,audits` order. Each named job must exist in the staging Dagster location. Candidate inspection remains available when this setting or a job is missing; the Observatory reports the required setup.

Every route requires exactly one `env=staging` query parameter. The API rejects `env=prod`, a missing or repeated `env`, and caller-supplied target overrides.

## Candidate and checks

`GET /api/v1/staging/promotions/candidate?env=staging` returns the pinned Git revisions, Nessie hashes, changed code paths, jobs, check-job configuration, candidate-bound check readiness, and table-copy inventory. Check readiness identifies unconfigured and missing jobs, absent evidence, evidence with mismatched location/ref/revision/hash/candidate bindings, failed and running checks, and Dagster evidence outages. The candidate ID covers its underlying code and data identity, not the changing readiness observation. The API rejects dirty, unrelated, or non-fast-forward worktrees. It also rejects symlinks, Git submodules, path escapes, and paths for secrets, dependencies, or generated state.

`POST /api/v1/staging/promotions/candidate/checks?env=staging` requires `lakehouse:operate`, an `Idempotency-Key` header, and this body:

```json
{"candidate_id": "<64-character candidate ID>"}
```

The request launches the three configured jobs against the mapped staging branch. Missing or stale prior evidence does not block this action; a fresh run is how operators create candidate-bound evidence. Each run is tagged with the staging environment, Nessie ref, Git revision, Nessie hash, and candidate ID. The API rejects the result if the candidate changes while the jobs run. Promotion separately requires all three checks to have successful evidence bound to the exact current candidate.

### Add executable check jobs

Phlo does not invent generic test, contract, or audit checks. Each project must select real pytest suites that represent those categories and are safe to run against staging. Do not select suites that promote data, mutate production, or otherwise write shared state. Install `pytest` in the Dagster code-location environment.

Add a workflow module that explicitly constructs the three jobs. This example uses existing Phlo unit and contract suites; review the selected paths for your environment before using them. In `workflows/promotion_checks.py`:

```python
from pathlib import Path

import dagster as dg
from phlo_dagster.promotion_checks import build_promotion_check_jobs

project_root = Path(__file__).resolve().parents[1]
defs = dg.Definitions(
    jobs=build_promotion_check_jobs(
        project_root=project_root,
        check_paths={
            "tests": ("tests/unit",),
            "contracts": ("tests/contracts/test_support_status_installed_artifact.py",),
            "audits": ("tests/unit/phlo/audit",),
        },
    ),
)
```

Replace the example paths with existing project suites; every path must exist beneath the project root. The factory creates real `pytest`-backed jobs named `phlo_promotion_tests`, `phlo_promotion_contracts`, and `phlo_promotion_audits`. Failed pytest runs fail their Dagster jobs; no check returns success without executing tests. Set the API's `PHLO_PROMOTION_DAGSTER_CHECK_JOBS` to those exact names, then reload the staging Dagster code location and verify the jobs appear in its job inventory. A project without suitable read-only pytest suites must first implement or select those project-specific suites; the factory cannot safely guess them.

Promotion-tagged jobs require the project root's Git HEAD to match `phlo/code_version` and its source worktree to be clean before pytest starts. They check HEAD and cleanliness again after pytest, refusing candidate evidence if either changed. Pytest runs without its cache or Python bytecode writes. The factory drains combined output and retains only an 8,000-byte tail within its configured timeout.

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
