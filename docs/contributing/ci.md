# CI contracts and rollout

Phlo separates **safe to merge**, **source health**, **artifact readiness** and
**maintenance health**. A passing source check is not release acceptance.

## Merge safety

`pr / required` remains the single required merge result. Pull requests select
affected package groups and reverse dependencies. Unknown inputs select full
validation; only explicitly inert documentation omits executable lanes. Every
merge group runs the full contract on its prospective merge SHA.

Required lanes cover Python quality and behaviour, installed provider artifacts,
PostgreSQL concurrency, quickstart and recovery, Windows portability,
Observatory, GitHub writer and webhook plugin behaviour, documentation,
dependency risk, container policy and focused mutation checks. A selected lane
that is skipped, cancelled or failed cannot satisfy the aggregate.

### Check buckets and stages

Checks share setup within four buckets. Named steps retain individual failure
logs; a failed step still fails its job. The buckets are not four literal jobs:
native Windows checks, service provisioning and slower test shards need separate
environments.

| Bucket | Execution layout |
| --- | --- |
| Quality and contracts | File hooks, workflow hardening and Python lint, format, types and reference checks share one job. Documentation builds retain their Node-based job. Focused mutation checks remain required when selected. |
| Behaviour | Core and package tests retain parallel shards. Observatory, GitHub writer and webhook plugin checks share one Node job, with each consumer selected independently. |
| Integration and portability | Storage/catalog suites, PostgreSQL concurrency, quickstart and recovery share one Linux workspace. Windows launcher and shared-layout checks share one native Windows job. |
| Artifact and dependency safety | Preparation builds wheels once for provider shards and Windows acceptance. Container waiver, Dockerfile lint and generated Compose checks share one job. Dependency assessments remain independent. |

| Stage | Scope |
| --- | --- |
| Pull request | Affected checks and reverse dependencies, introduced dependency risk, conservative full coverage for unknown changes. |
| Merge queue | All buckets on the exact prospective merge SHA, including the full dependency policy. |
| Main and beta | Authenticate and reuse full queue evidence. Run the full fallback only when evidence cannot be reused. |
| Scheduled maintenance | Fresh dependency and upstream-image scans, mutation and extended reliability checks. Findings enter the normal remediation PR flow. |
| Release staging | Require source health and fresh release dependency evidence, then build and scan immutable release artifacts. |
| Release acceptance and promotion | Exercise the staged bytes on required platforms and repeated runs, then publish those bytes through an explicit manual dispatch. |

`pr / required` collects combined coverage after both Python and service
contracts finish, then records queue evidence. Coverage does not need its own
runner. No test suite, dependency gate or release approval is removed by the
consolidation. Standalone Windows dispatch builds its own wheels; ordinary
validation reuses the preparation artifact.

Integration provisions disposable PostgreSQL, MinIO and Nessie. Storage/catalog
suites reject missing tests, skips and expected failures through
`scripts/ci_required.py`; PostgreSQL guards require exactly three passes without
skips. Local tests can still skip unavailable services without this opt-in
plugin. Reproduce the storage/catalog suites with
`uv run --locked python scripts/run_integration.py`;
Docker is required and provisioned containers are removed afterwards.

Core, regression, package and quickstart coverage is combined into one report.
Quickstart runs once in its artifact-producing lane, not twice. Coverage is
measurement, not an arbitrary global percentage gate. Establish changed-code
budgets from the measured baseline before adding a threshold.

Mutation checks use `scripts/check_mutation.py`, because `mutmut run` exits zero
even with surviving mutants. Missing results, uncovered mutations, timeouts,
skips and unreviewed survivors fail. `security/mutation-exceptions.json` names
specific equivalent or diagnostic-only changes, with rationale and exact diff
hashes; changing a mutant invalidates its exception. Diagnostic-only is not the
same as semantically equivalent. Review each exception as part of this policy.

## Source-health evidence

Successful full queue runs emit `phlo.merge-validation/v1`: exact SHA, repository,
workflow, run attempt, policy digest, expanded matrix results, job timings and a
checksum-bound complete dependency assessment. Main/beta pushes authenticate
the producer, artifact checksum, exact attempt and live job results. Selected PR
evidence, incomplete matrices, changed policies and assessments older than
24 hours are rejected. Missing evidence always runs full validation.

Main automatically reuses authenticated full queue evidence for the identical
commit. No rollout variable or maintainer action is required. The full fallback
still runs when evidence is missing, invalid or expired, including direct pushes
and merges that produce a different SHA.

## Dependencies and upstream ownership

All tracked Python and npm consumer locks, including examples, use one
revision-bound OSV inventory. PRs block introduced risk at every severity; queues
also block existing findings without a reviewed, bounded exception. Releases
block every finding. Scanner failure is unavailable evidence, not a clean scan.
See the [dependency policy](../../security/dependency-policy.md).

The independent daily Security run hands authenticated JSON findings to one
deduplicated security issue per advisory/package/version, or comments on an open
PR explicitly addressing that advisory and package. Ordinary version updates
do not suppress intake. It preserves all affected consumer paths without
repeating unchanged comments. Missing or unavailable assessments create a
maintainer-owned escalation. Phlo agent owns triage and coordination with
Renovate; neither owns the scanner verdict or approves its own risk exception.

Existing signed webhook intake ignores bot senders. Configure a human-owned,
least-privilege `PHLO_DEPENDENCY_TRIAGE_TOKEN` with repository Issues write,
Pull requests write, Actions read and Contents read to enable that intake. Without
it, the workflow uses `github.token` to record issues, but maintainers must triage them. Verify a
real signed webhook delivery before claiming automatic agent handoff. No token,
webhook or agent schedule is provisioned by this change.

## Artifact readiness and publication

Ordinary main pushes are not release candidates. ReleaseX still proposes the
version PR. After it merges, dispatch **Release Stage** with a full main SHA.
Staging first requires a fresh release-mode dependency assessment and successful
exact-SHA main source health, then reserves the candidate once. It builds package
distributions once, reuses those wheels in native image builds, scans images,
pins their digests and records a BOM and provenance. A failed or expired staging
attempt does not permit restaging that SHA.

Dispatch **Release Artifact Acceptance** with the candidate SHA and staging run.
Clean amd64 and arm64 hosts exercise staged artifacts, never source builds.
Missing BOMs fail. Promotion authenticates every candidate acceptance attempt,
including failures, and retains ADR 0050's three hosts, three qualifying runs and
two-day span. No reduced release policy is introduced.

**Release Promotion** defaults to a dry run. Set `execute: true` on a fresh
manual dispatch to publish. GitHub authenticates the dispatch actor, and promotion
verifies the live workflow run on protected `main`. The `release` environment
does not require reviewers or prevent-self-review protection. Configure the
existing `PYPI_API_TOKEN` publication credential separately. Promotion publishes
the authorized staged bytes without rebuilding and reconciles published digests.
Development images use a separate `-development:<SHA>` namespace.
Only an explicit Release Stage dispatch builds and uploads these staging images.
Ordinary main pushes cannot invoke the image publisher. PR, queue and main checks
reuse installed-provider image builds for local scans by image ID, with no
registry writes or duplicate builds.

MinIO is an independently published provider dependency. **Publish MinIO Image**
is a separate manual dispatch on `main`, with its own image version and no PyPI
publication or whole-release acceptance gate. Both publishers reuse
`build-service-images.yml` for native builds, immutable-digest scans and manifest
assembly. The MinIO publisher also tests health and an object roundtrip on each
architecture, and refuses to overwrite a published tag. Phlo staging records
the existing MinIO digest in its BOM without rebuilding or promoting the image.
ReleaseX does not bump MinIO image tags. Nightly rescans include the latest
independently published MinIO image as well as the released Phlo fleet.
See [MinIO image publication](../../packages/phlo-minio/README.md#publish-an-image-only-fix).

The publication plan binds the exact BOM bytes and qualifying evidence set.
Execution reauthenticates both; changes require a fresh dispatch.
Promotion reruns are rejected; authorization belongs to the original dispatch
and run attempt. After a partial publication, dispatch again with the same
staging identity: matching public tags, package files, images and release assets
are reused; missing ones complete forward. Conflicting bytes or unverifiable
public digests fail closed. Never restage or rebuild to recover publication.

## Rollout and measurement

1. Merge dependency remediation [#1063](https://github.com/phlohouse/phlo/pull/1063)
   first. Do not grandfather its vulnerable baseline or add resolver overrides.
2. Merge the CI contract changes through the existing required queue check.
3. Observe reuse decisions and required job inventories on real GitHub runs.
   Validate Windows/native arm64 there; an x64 orb cannot prove those platforms.
4. Configure release credentials and optional human agent-intake identity.
   Exercise staging, acceptance and dry-run promotion before publishing.
5. Collect two weeks of timings, queue delay, first actionable failure, cache
   hit rate and retries before tuning shard weights.

Target selected-PR median ≤5 minutes and p95 ≤8; full-queue median ≤8 and p95
≤12. These are measurement goals, not verified savings. Preserve behavioural
contracts while optimising bootstrap and proven duplication. Do not retry a
correctness failure until it passes or silently quarantine a critical contract.
