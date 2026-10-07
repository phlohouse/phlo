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

Integration provisions disposable MinIO and Nessie and rejects missing tests,
skips and expected failures through `scripts/ci_required.py`. Local tests can
still skip unavailable services without this opt-in plugin. Reproduce the
provisioned lane with `uv run --locked python scripts/run_integration.py`;
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

Reuse starts in **shadow mode**: main still runs full checks and records its
reuse decision. After comparing real queue/main pairs, an authorised maintainer
can set repository variable `CI_REUSE_QUEUE_EVIDENCE=true` to omit only the
identical full source rerun. No variable is changed by this implementation.

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

**Release Promotion** defaults to a dry run. Execution requires the live `release`
environment to have named user reviewers and prevent self-review, and verifies
the approving human through GitHub. Configure this protection before release
execution; an unprotected environment fails closed. Configure the existing
`PYPI_API_TOKEN` publication credential separately. Promotion publishes the
approved staged bytes without rebuilding and reconciles published digests.
Development images use a separate `-development:<SHA>` namespace.

The pre-approval plan binds the exact BOM bytes and qualifying evidence set.
Approval reauthenticates both; changes require a fresh dispatch and approval.
Promotion reruns are rejected because GitHub's approval API does not bind reviews
to a run attempt. After a partial publication, dispatch again with the same
staging identity: matching public tags, package files, images and release assets
are reused; missing ones complete forward. Conflicting bytes or unverifiable
public digests fail closed. Never restage or rebuild to recover publication.

## Rollout and measurement

1. Merge dependency remediation [#1063](https://github.com/phlohouse/phlo/pull/1063)
   first. Do not grandfather its vulnerable baseline or add resolver overrides.
2. Merge the CI contract changes through the existing required queue check.
3. Observe shadow decisions and required job inventories on real GitHub runs.
   Validate Windows/native arm64 there; an x64 orb cannot prove those platforms.
4. Configure protected release approval and optional human agent-intake identity.
   Exercise staging, acceptance and dry-run promotion before publishing.
5. Collect two weeks of timings, queue delay, first actionable failure, cache
   hit rate and retries before tuning shard weights or enabling evidence reuse.

Target selected-PR median ≤5 minutes and p95 ≤8; full-queue median ≤8 and p95
≤12. These are measurement goals, not verified savings. Preserve behavioural
contracts while optimising bootstrap and proven duplication. Do not retry a
correctness failure until it passes or silently quarantine a critical contract.
