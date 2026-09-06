# PR validation, merge queue, and release protection

`pr / required` is the pre-merge gate for `main` and `beta`. The PR Validation
workflow runs on both `pull_request` and `merge_group`, with no workflow-level
path exclusions. It requires CI, integration, container contracts, workflow
hardening, and the dependency risk comparison to succeed. Failed, cancelled,
missing, or unexpectedly skipped work cannot pass the aggregate.

Full core and package tests run on Python 3.11 and 3.12 before merging. Core
modules are partitioned into three deterministic shards per interpreter; module
fixtures stay together. Core regression checks run once per interpreter.
Frontend and agent tests/builds, PostgreSQL concurrency checks, installed-provider
Docker verification, and the recovery drill remain pre-merge requirements.
JUnit timings and per-shard coverage are retained as artifacts; individual shard
coverage is not a whole-suite coverage percentage.

## Where work runs

| Event | Work | Gate |
| --- | --- | --- |
| PR update | Full correctness checks plus introduced dependency risk; cancel obsolete revisions of that PR | `pr / required` |
| Merge queue | The same checks on GitHub's merge-group SHA, compared with its base SHA | `pr / required` |
| Push to main/beta | One release-candidate pipeline calling CI, integration, whole-tree security and nightly acceptance | `release candidate / status` |
| Daily | Whole-tree dependency audits, complete published-image rescans, nightly integration/release acceptance, fresh installed-provider dependency resolution | Remediation and release readiness; not unrelated PRs |
| Manual release qualification | Same release-candidate pipeline on the selected SHA | `release candidate / status` |
| Release publication | Existing exact-SHA and artifact evidence checks | Publication |

Reusable CI and integration no longer trigger independently on PRs or branch
pushes. The extra lockfile-triggered nightly caller is removed. Release Candidate
no longer triggers on PRs or merge groups. Post-merge qualification deliberately
rechecks the exact merged SHA because the existing publisher consumes that
identity; pre-merge correctness is never deferred to it.

Provider wheels are built once per CI run and downloaded by all four verification
shards. External Python requirements are constrained by the checked-in workspace
lock. The daily fresh-resolution probe exercises declared dependency ranges
separately. These two checks answer different compatibility questions.

## Dependency policy

`scripts/dependency_delta.py` reads `uv.lock` and both npm application lockfiles
from exact Git revisions without installing dependencies or executing either
revision. It queries the union of package versions through OSV and uses each
result for both sides of the comparison. An advisory newly published against an
unchanged version is existing debt, not introduced risk. This uses shared results
for identical versions; it does not claim that the OSV service offers an atomic,
globally frozen database snapshot.

All newly introduced vulnerable registry versions block, regardless of severity.
This includes upgrading to another affected version or introducing an existing
vulnerable package into a different product. Existing findings remain in the
assessment artifact. Missing locks, unsupported formats/sources, scanner errors,
and incomplete responses fail as an unavailable assessment, never as clean.
Network requests have timeouts and at most three attempts. The gate currently
supports registry PyPI and npm dependencies; extending source types requires an
explicit parser and policy change, not silent exclusion.

Whole-tree `uv audit` and `npm audit --audit-level=high` still run daily and during
release qualification, including the Eve npm application. Published-image
rescans collect every image's result before failing their aggregate. PR waiver
validation checks structure; expiry is enforced in scheduled validation. The
current waiver report is generated into the Actions summary rather than compared
with a date-sensitive committed snapshot.

The repository maintainer owns daily Security, Container Rescan, and Nightly
failures. Enable GitHub Actions failure notifications for those workflows. Triage
scanner outages separately from vulnerabilities; record actionable findings with
an owner and remediation deadline in the issue tracker. Known exploitation or
critical exposure requires immediate assessment, including when no fix exists.
A green PR is not permission to release existing unaccepted vulnerability debt.
Existing release vulnerability policy and promotion authorization remain intact.

## Activating the merge queue after this change lands

The versioned payload is
[`security/release-candidate-ruleset.json`](../../security/release-candidate-ruleset.json).
Committing this file does **not** activate GitHub settings. Apply it only after
`pr.yml` exists on `main` and the PR gate has passed, otherwise GitHub cannot
emit merge-group checks and the queue will stall.

The payload retains the existing template's fresh approving-review requirement
(one approval, including approval of the last push), prohibits force pushes and
deletions, and requires `pr / required` from GitHub Actions. It replaces the
placeholder emergency bypass team with no bypass actors. A separate reviewer
must therefore be available; author self-approval cannot satisfy this rule.

Queue defaults are squash merge, ALLGREEN, one group building at a time, one PR
per merge, and a 60-minute check timeout. The queue tests against current main,
so strict manual branch-up-to-date enforcement is disabled. Reusable children
have no concurrency groups; PR cancellation lives only in their orchestrator.

An administrator should first inspect current settings:

```bash
gh api repos/phlohouse/phlo/rulesets
gh api repos/phlohouse/phlo/branches/main/protection
```

If there is no existing matching ruleset, create one after the bootstrap PR has
merged:

```bash
gh api --method POST repos/phlohouse/phlo/rulesets \
  --input security/release-candidate-ruleset.json
```

If a matching ruleset already exists, preserve unrelated settings and update its
ID rather than creating a duplicate. These commands change repository settings;
they are an activation procedure, not something CI executes.

Validate activation with one harmless PR: check `pr / required`, add the approved
PR to the queue, confirm the same check appears on a distinct merge-group SHA,
and inspect the successful squash merge. Also verify that failed/cancelled
checks remove or hold an entry instead of permitting a merge. Keep the run URLs
and a fresh ruleset snapshot. Do not add unrelated PRs to the queue for testing:
queue enrollment authorizes automatic merging once requirements pass.

## Release and recovery

The publisher builds the whole workspace, so a new root release must assign a
fresh version to every package under `packages/`, even if its source is unchanged.
Rebuilding an already published version is not guaranteed to reproduce its exact
archive hash. `workspace.cascade_bumps = true` makes ReleaseX include dependent
packages when core changes; the required Python quality job compares a proposed
release against the previous reachable root version tag and rejects any reused
or regressed package version. Ordinary development without a root version change
does not require version bumps. This check performs no PyPI requests.

The 0.15.1 corrective release advances all 38 published projects after the 0.15.0
publish preflight rejected rebuilt 0.14.0 artifacts. It preserves the existing
0.15.0 tag and published PyPI artifacts. Remote artifact identity checking remains
mandatory immediately before upload; never bypass a hash conflict with a generic
skip-existing option.

`release candidate / status` continues to require CI (including every provider
shard and recovery drill), integration, security, and the release golden path for
the same candidate SHA. It uploads `release-candidate-evidence-<sha>`.

`release-tag` polls for that successful status on the exact pushed release SHA
before invoking ReleaseX. Publication repeats the check against the tag target.
Neither accepts stale, branch-level or skipped evidence. The separate staged
artifact promotion workflow and release-owner authorization remain unchanged.

## Validation

Run `make actionlint`, `make zizmor`, `python3 scripts/check_ci_package_groups.py`,
`python3 scripts/validate_support_manifest.py`, and the focused workflow, shard,
dependency delta, and provider tests under `tests/tooling` and `tests/scripts`.
Before changing shard count, verify the shard union equals the complete selected
test inventory and no test belongs to two shards. Use uploaded timings to adjust
balance; do not remove tests to reach a timing target.
