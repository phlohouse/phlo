# Dependency qualification, 11 October 2026

This record covers the remaining scope of [#1021](https://github.com/phlohouse/phlo/issues/1021).
The dependency base is [main at 0b8685e6](https://github.com/phlohouse/phlo/commit/0b8685e66cb9a299fa1a3c62adf9cacfec0505be).
No pending PR is a prerequisite. In particular, #1096's import-linter changes
and #1107's SQL quality dependency are not imported. CI gates and API product
contracts remain with their respective owners.

## Fresh registry inventory

Versions below come from live PyPI JSON and npm registry queries on 11 October
2026, not the historical issue table. "Before" means the root workspace or
Observatory lock, not every example project's independent lock.

| Dependency | Before | Latest stable | Decision |
| --- | --- | --- | --- |
| pyarrow | 23.0.1 | 26.0.0 | Qualify 26.0.0 with the current provider stack |
| Observatory TypeScript | 5.9.3 | 7.0.2 | Use peer-supported 6.0.3; defer 7 |
| FastAPI | 0.136.3 | 0.143.0 | Retain `<0.137`; latest fails existing route contracts |
| PyIceberg | 0.11.1 | 0.12.0 | Retain `<0.12`; latest breaks optimistic-concurrency contracts |
| dlt | 1.31.0 | 1.31.0 | Keep delivered version |
| pandas | 3.0.6 | 3.0.6 | Keep delivered version |
| pydantic | 2.14.0 | 2.14.0 | Keep delivered version |
| dbt-core | 1.12.5 | 1.12.5 | Keep delivered version |
| Dagster | 1.13.26 | 1.13.26 | Keep delivered version |
| Vite | 8.3.2 | 8.3.4 | Major upgrade already delivered; no repeat patch bump |
| TanStack React Start | 1.168.60 | 1.168.61 | Historical target delivered; no repeat patch bump |

The Python authorities are `https://pypi.org/pypi/<name>/json` and each
version's `https://pypi.org/pypi/<name>/<version>/json`. The npm authorities
are `npm view <name> version`, `npm view typescript dist-tags --json`, and
`npm view <name>@<version> peerDependencies dependencies --json`.

## Arrow 26 with PyIceberg, dlt and pandas

[PyIceberg 0.11.1 metadata](https://pypi.org/pypi/pyiceberg/0.11.1/json)
requires Arrow `>=17` for its Arrow extras.
[dlt 1.31.0 metadata](https://pypi.org/pypi/dlt/1.31.0/json) requires Arrow
`>=16` for Parquet and PyIceberg destinations, and PyIceberg `>=0.9.1`.
[pandas 3.0.6 metadata](https://pypi.org/pypi/pandas/3.0.6/json) requires
Arrow `>=13` for its Arrow and Parquet extras. None excludes Arrow 26.
Arrow 26 requires Python `>=3.11`, which includes Phlo's Python 3.12 baseline.

The ingestion, Iceberg and test harness manifests require `pyarrow>=26.0.0,<27`.
The upper bound keeps the next unqualified major out of these shared provider
paths. Other providers retain their existing requirements, but consume Arrow
26 in the workspace lock. Only Arrow changes in the Python registry inventory.

The initial isolated Arrow probe passed 937 tests across Iceberg, dlt, Pandera,
Delta, ClickHouse, Sling and Polaris, with 12 integration-marked cases deselected.
This is consumer coverage, not proof of every external provider deployment.

## FastAPI and PyIceberg deferrals

The caps originated in [#1097](https://github.com/phlohouse/phlo/pull/1097).
Reassessment uses the latest stable releases in disposable `uv run --with`
overlays. These probes do not change the committed constraints or install an
unsupported version in the delivered environment.

With only FastAPI 0.143.0 overlaid, `test_route_inventory.py` and
`test_security_manifest.py` report **23 failed, 82 passed**. The mounted-route
comparison, new unguarded mutation detection, guarded-handler lookup and
mechanical manifest coverage tests still assume eagerly exposed routes.
The existing lazy-router support test alone does not establish compatibility
with the latest actual framework. Retain the cap until the complete route and
security contract is qualified with real lazy included routers. Do not silence
these tests or treat missing inventory as successful coverage.

With only PyIceberg 0.12.0 overlaid, `test_merge_atomic.py`, `test_history.py`
and Polaris `test_release_cas.py` report **12 failed, 47 passed**. These use a
real local SQL catalog and competing commits, not mocked commit exceptions.
PyIceberg logs automatic retries against concurrent updates. Two stale ledger
writers can commit where Phlo expects the loser to raise `ReleaseConflictError`.
History policy binding and complete-batch reconciliation also fail. Retain
the cap until application-controlled retry and compare-and-swap guarantees
survive the new transaction semantics. Passing ordinary append tests is not
enough to remove this bound.

Reproduce each isolated probe from this branch:

```bash
uv run --locked --with 'fastapi==0.143.0' pytest \
	packages/phlo-api/tests/test_route_inventory.py \
	packages/phlo-api/tests/test_security_manifest.py --tb=short
uv run --locked --with 'pyiceberg==0.12.0' pytest \
	packages/phlo-iceberg/tests/test_merge_atomic.py \
	packages/phlo-iceberg/tests/test_history.py \
	packages/phlo-polaris/tests/test_release_cas.py --tb=short
```

## TypeScript 6, not an unsupported TypeScript 7 override

[typescript-eslint 8.71.1](https://registry.npmjs.org/typescript-eslint/8.71.1)
declares TypeScript `>=4.8.4 <6.1.0` and ESLint `^8.57.0 || ^9.0.0 || ^10.0.0`.
The old 8.48.1 override requires TypeScript `<6.0.0`.
The existing TanStack ESLint config accepts the updated 8.x tooling.
The existing overrides now pin typescript-eslint 8.71.1 and ts-api-utils 2.5.0,
with TypeScript `~6.0.3`. No forced install, legacy peer mode, warning
suppression or widened upstream peer declaration is needed.

The Worker can retain TypeScript 7.0.2 because it does not share this ESLint
consumer graph. Observatory is now one major behind the latest TypeScript,
with an explicit deferral until the parser supports 7. Existing TanStack
React Start 1.168.60, Router, Vite 8.3.2 and Nitro remain unchanged.
Typecheck, lint, all 115 tests in 24 files and the production build pass.
`npm ls typescript typescript-eslint ts-api-utils --all` reports no invalid peers.

## Qualification evidence and limits

- `make setup` and the initial `make check` pass. The Python run reports
  6508 passed, four existing skips and 231 integration cases deselected.
- The post-upgrade full Python suite also reports 6508 passed, four skips and
  231 deselected. All other `make check` lanes pass except an initial formatter
  check against the concurrently running golden path's temporary generated
  `workflows/schemas/csv.py`. After the harness removes its project,
  `uv run --locked ruff format --check .` passes with 1604 files already formatted.
  No tracked file was reformatted and no check was suppressed.
- `uv sync --locked` installs Arrow 26.0.0 with PyIceberg 0.11.1, dlt 1.31.0,
  pandas 3.0.6 and FastAPI 0.136.3.
- The same security-route, history, merge and Polaris contract files used for
  the negative upgrade probes pass all 164 tests with the delivered lock.
- `uv run --locked python scripts/run_integration.py` passes all eight required
  lanes, with **113 tests, zero failures, zero errors and zero skips**. The lanes
  are Iceberg, Dagster, API, MinIO migration, dbt quality, schema generation,
  schema conversion and ingestion. This includes real MinIO, Nessie,
  PostgreSQL and Trino provider execution, not just unit tests.
- `uv run --locked python scripts/release_golden_path.py` ends with
  **`release golden path passed`**. Source wheels and the exported locked
  constraints drive an owned disposable stack. CSV ingestion and dbt both
  produce two queried rows. WAP materialisation succeeds and its live Dagster
  run acquires the promotion tag before the harness removes the stack.
  This source-mode run does not exercise candidate-BOM rejection reports or
  production preflight, which belong to the release-candidate qualification.
  Direct package-version inspection in the running Dagster container confirms
  Arrow 26.0.0, PyIceberg 0.11.1, dlt 1.31.0, pandas 3.0.6, Dagster 1.13.26,
  dbt-core 1.12.5 and pydantic 2.14.0. The API container uses FastAPI 0.136.3.
- Observatory `npm ci --strict-peer-deps`, `npm ls --all`, typecheck, lint,
  all 115 frontend tests and build pass. `npm audit --audit-level=high` reports
  zero vulnerabilities. Existing jsdom canvas and bundler directive warnings remain.
- The dependency-delta queue assessment covers all 15 tracked consumer locks,
  including independent examples and the unchanged Worker. It reports no
  existing, introduced or unaccepted vulnerabilities. Those independent locks are not
  silently refreshed as part of this issue.
- Support manifest validation, reference-doc drift, Markdown links, codespell
  and whitespace checks pass. A built core wheel contains the byte-identical
  authoritative support manifest.

Local verification does not replace required CI or establish production
readiness, native platform support, or unrelated pending PR compatibility.
The demonstrated FastAPI, PyIceberg and TypeScript 7 deferrals remain explicit
support decisions, not untested upgrades or claims that the latest versions work.
