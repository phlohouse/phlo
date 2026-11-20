# ADR 0064: Separate CI contracts with one required result and no silent skips

## Status

**Accepted (back-filled 2026-10-30)**

Amended (2026-11-20): pull requests run each test once on the 3.12 baseline.
Python 3.13, Windows portability and the Trivy scan of built images move to
the scheduled Nightly and Release Rehearsal, and mutation becomes a weekly
report. A new source tree meets a default coverage floor instead of failing.
The merge queue, the single required check and the no-silent-skips rule are
unchanged. See decisions 5 and 8.

- Original decision: September–October 2026
- Evidence: issue [#999](https://github.com/phlohouse/phlo/issues/999) (closed
  2026-09-25, select PR CI by change); [#1068](https://github.com/phlohouse/phlo/pull/1068)
  (merged 2026-10-07, "separate merge, maintenance and immutable release
  contracts"); issue [#998](https://github.com/phlohouse/phlo/issues/998) and
  [#1111](https://github.com/phlohouse/phlo/pull/1111) (merged 2026-10-11,
  measured coverage floors and required-service enforcement)
- Decision owner: Phlo project maintainers

## Context

By September 2026 one CI pipeline mixed checks that answer different questions:
whether a change is safe to merge, whether `main` is healthy, whether scheduled
maintenance found new problems, and whether release artifacts are ready. A pass
in one was easily read as a pass in another. Tests that needed PostgreSQL, MinIO
or Nessie could skip when the service was unavailable, so a "green" lane could
have exercised nothing. CI ran every lane for every change.

## Decision

1. **Four contracts.** CI separates *safe to merge*, *source health*,
   *artifact readiness* and *maintenance health*. A passing source check is not
   release acceptance.
2. **One required merge result.** `pr / required` is the single required check.
   A selected lane that is skipped, cancelled or failed cannot satisfy it.
3. **Select by change, conservatively.** Pull requests run affected package
   groups and their reverse dependencies. Unknown paths select full validation;
   only explicitly inert documentation omits executable lanes. Every merge-queue
   run executes the full contract on the exact prospective merge commit.
   `make check-changed BASE=origin/main` runs the same selection locally.
4. **Required suites cannot skip.** Named required lanes run with
   `scripts/ci_required.py`, which fails on skips, expected failures and empty
   selections. The eight integration contracts provision real PostgreSQL, MinIO
   and Nessie and reject skips. A test under an `integration` directory must carry
   the integration marker.
5. **Measured coverage floors.** Line and branch coverage are measured for core,
   scripts and every package source tree, and each has a floor in
   `tests/coverage-floors.json`. A new source tree without a floor must meet
   the default floor (70% line, 50% branch). A floor without a measurement fails.
6. **Main reuses authenticated queue evidence.** A successful full queue run emits
   `phlo.merge-validation/v1` evidence bound to the exact SHA, attempt, policy
   digest and dependency assessment. A push to `main` reuses it only when it is
   authentic and fresh, and otherwise runs the full fallback.
7. **Release acceptance is separate.** The release builds once and smokes the
   built artifacts, as defined in [ADR 0068](0068-release-publishing.md), which
   supersedes ADR 0050. Release Rehearsal runs the same jobs before release day.
8. **Mutation results are a weekly report.** The scheduled mutation run keeps
   `scripts/check_mutation.py` and its reviewed exceptions, but it no longer
   gates pull requests.
9. **One pass per test on PRs.** PRs run the 3.12 baseline. Python 3.13,
   Windows portability and built-image scans run nightly, and a supported
   Python version must appear in `ci.yml` or `nightly.yml`.

## Consequences

- Contributors read one result, and a skipped required suite shows as a failure,
  not a pass.
- Local `make check` is a baseline, not CI. The
  [verification matrix](../../contributing/verification-matrix.md) lists the extra
  checks for each changed surface.
- Adding a new package or source tree requires adding it to the CI package
  groups and the coverage floors.
- Evidence from a selected PR run, an incomplete matrix or a changed policy, or
  an assessment older than 24 hours, is rejected. CI then runs the full fallback.

## Alternatives Considered

- **Running every lane for every pull request:** replaced by change selection
  with full validation in the merge queue (#999, #1068).
- **Allowing service-backed tests to skip in CI:** rejected; a skipped required
  suite proves nothing.
- **Treating a green `main` as release readiness:** rejected; release acceptance
  exercises the exact staged bytes.

## Related

- [ADR 0068](0068-release-publishing.md): release build, smoke and publishing.
- [ADR 0063](0063-dependency-and-container-vulnerability-policy.md): dependency and container gates within these contracts.
- [CI contracts and rollout](../../contributing/ci.md), [test and contract gates](../../contributing/testing-gates.md).
