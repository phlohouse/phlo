# ADR 0062: Cap dependencies that break contracts, and qualify before lifting

## Status

**Accepted (back-filled 2026-10-30)**

- Original decision: October 2026
- Evidence: [#1097](https://github.com/phlohouse/phlo/pull/1097) (merged 2026-10-10,
  caps introduced); issue [#1021](https://github.com/phlohouse/phlo/issues/1021) and
  [#1110](https://github.com/phlohouse/phlo/pull/1110) (merged 2026-10-11, qualification);
  [dependency qualification record](../../../registry/support/dependency-qualification.md)
- Decision owner: Phlo project maintainers

## Context

Renovate and batched upgrade PRs keep third-party dependencies current. In
October 2026 two upstream minor releases changed behaviour that Phlo's contracts
depend on, while ordinary tests still passed:

- **FastAPI 0.137** registers included routers lazily, so `app.routes` no longer
  lists the mounted API routes. Phlo's route inventory and security-manifest
  coverage walk then see almost nothing, and phlo-api tests fail. Silencing them
  would leave unguarded mutations undetected.
- **PyIceberg 0.12** retries conflicting commits itself and raises a different
  exception. Concurrent writers that Phlo expects to be rejected are no longer
  rejected, so Iceberg history and merge-conflict tests and the Polaris release
  compare-and-swap tests fail.

#1097 held both back. #1110 then reassessed the latest releases in disposable
`uv run --with` overlays: FastAPI 0.143.0 gave 23 failures in the route and
security contract files, and PyIceberg 0.12.0 gave 12 failures in the merge,
history and Polaris compare-and-swap files.

## Decision

1. **Cap a dependency when a release breaks a Phlo contract.** The root
   [pyproject.toml](../../../pyproject.toml) and the consuming package manifests
   declare FastAPI `<0.137` (root and phlo-api) and PyIceberg `<0.12` (root,
   phlo-iceberg, phlo-polaris, phlo-dlt and phlo-testing), so a lock refresh
   cannot reintroduce the release.
2. **Qualify before lifting.** A cap is removed only after the owning contract
   tests pass against the new release with real behaviour, not by weakening or
   skipping those tests. For PyIceberg, that means application-controlled retry
   and compare-and-swap guarantees survive the new transaction semantics.
   Passing ordinary append tests is not enough.
3. **Record each qualification.** The qualification record lists the dated
   registry inventory, each decision (upgrade, keep or defer), the overlay command
   that reproduces each failure, and the evidence for upgrades that were made.
4. **Bound unqualified majors on shared paths.** Shared provider paths declare an
   upper bound below the next major they have not qualified, for example
   `pyarrow>=26.0.0,<27` for ingestion, Iceberg and the test harness. Orchestration
   and transformation dependencies follow the same pattern (`dagster>=1.13,<1.14`,
   `dbt-duckdb>=1.9,<1.12`).

## Consequences

- Phlo can be one or more minor releases behind FastAPI and PyIceberg. Each
  deferral is an explicit support decision, not an untested upgrade.
- A lock refresh cannot pick up a capped release. Lifting a cap is a deliberate
  manifest change that the contract tests must accept.
- To lift a cap, rerun the documented overlay probes, fix the owning contract,
  and update the qualification record.
- Each example project's independent lock is assessed for vulnerabilities but is
  not refreshed as part of a qualification.

## Alternatives Considered

- **Upgrade and adjust the failing tests:** rejected. The failures show lost
  route-security coverage and lost concurrency rejection, not stale tests.
- **Pin exact versions:** not adopted; ranges allow patch and compatible minor
  updates below the cap.
- **Rely on `uv.lock` alone:** rejected in #1097 because a lock refresh could
  reintroduce the incompatible release.

## Related

- [ADR 0063](0063-dependency-and-container-vulnerability-policy.md): vulnerability policy for the same dependencies.
- [ADR 0058](0058-support-manifest-as-support-authority.md): support claims these caps protect.
