# Contributing to Phlo

Thank you for contributing to Phlo. Please discuss substantial changes in an
issue before opening a pull request.

## Set up a development checkout

Install Python 3.12, `uv`, and Node.js 24 or later. Clone the repository, then
run `make setup` from its root. This creates the Python environment and installs
the locked Observatory and Phlo GitHub writer dependencies. Run `make check`
for the local baseline. Use the [verification matrix](docs/contributing/verification-matrix.md)
to select additional checks for the surfaces you changed.

The [release workflow](.github/workflows/release.yml) and [release promotion
contract](docs/architecture/decisions/0050-freeze-release-promotion-contract.md)
describe the release process.

## Contribution licence

By submitting a contribution, you confirm that you have the right to submit it
and agree to license it under AGPL-3.0-or-later.

For substantial external contributions, we may ask you to sign our Contributor
Licence Agreement so that Phlo can also offer commercial licences.

## Comments and docstrings

Phlo follows a concise, technically direct commenting style inspired by the
Redis source tree: comments explain why, contracts state guarantees, and
obvious code stays uncommented. The binding rules are below.

### Module headers

Add a top-of-file block when you create or change a `.py`, `.ts`, `.tsx`, `.js`,
`.sql`, or `.sh` file. For Python use a module docstring as the first
statement; for TypeScript use a `/** ... */` block; for SQL use `--` lines;
for shell use a `#` block directly after the shebang.

- One sentence of purpose, then only facts evident from the code: contracts,
  invariants, ownership, ordering or failure semantics. Never invent intent.
- Two to six lines for substantive modules; one honest line for thin ones.
- Vendored or generated files may opt out with `phlo: no-header` on line 1.

The `check-file-headers` pre-commit hook checks changed files for a header.
It does not check every tracked file.

### Function and class docstrings

Write a one-line imperative contract as the summary line. Add short body
lines only when they carry information the signature does not: non-obvious
argument semantics, return and error behaviour, ordering guarantees,
lifecycle rules, ownership transfer.

- Do not use `Args:`/`Returns:`/`Parameters:` sections that restate
  parameter names or types; these ceremonial blocks are not used in Phlo.
- State raised errors inline as `Raises: SomeError when <condition>.`
- Executable doctest examples are welcome and run in CI via
  `tests/test_doc_examples.py`.
- Private helpers (`_`-prefixed) and self-evident one-liners may stay bare;
  never add a docstring that merely paraphrases the name.
- Test functions are exempt: the test name is the scenario description.


## Testing standards

Tests defend observable contracts: a test fails when user-facing behaviour
regresses, and passes for refactors that preserve it. The binding rules:

- **Behavioral oracles.** Assert outcomes through public APIs — returned
  data, written files (parsed), exit codes, emitted events, HTTP responses.
- **No static-artifact mirroring.** Never assert substrings inside checked-in
  Dockerfiles, workflow YAML, Makefiles, or config templates. Parse them
  (`yaml.safe_load`, instruction lines) and assert structure, or execute the
  behaviour. Generated output must be parsed or imported, not grepped.
- **Every test can fail.** No `assert x or not x`, no `exit_code in [0, 1]`,
  no assertions guarded by `if` on the value under test, no assertion-free
  calls. Pin the expected outcome per case.
- **Mocks sit at real seams.** A fake stands in for an external system
  (HTTP, subprocess, database driver) while real code runs around it.
  Asserting on mock call arguments only restates implementation; prefer
  recording fakes whose outputs derive from their inputs.
- **Markers.** Default runs (`make test`) exclude `-m integration`. Mark a
  test integration only when it needs an external service, container, or
  network; never to park slow unit tests — fix them instead.
- **Isolation.** Use `tmp_path` (never `tempfile`), inject clocks instead of
  sleeping, restore patched globals (`sys.modules`, caches, singletons) via
  fixtures, and derive repo-shape counts from the inputs rather than hardcoding.
- **Shared contracts live once.** Cross-package adapter/resolver contracts
  come from `phlo_testing.authorization_surface`; do not re-copy them into
  package suites.

Reference suites for each pattern: `tests/observability/test_run_reconciliation.py`,
`packages/phlo-iceberg/tests/test_tables_rollback.py`,
`packages/phlo-api/tests/test_security_manifest.py`,
`packages/phlo-dagster/tests/test_oidc_identity.py`,
`packages/phlo-postgres/tests/test_postgres_cli.py`.

## Complexity

Keep new Python and Observatory TypeScript functions at cyclomatic complexity
15 or below. Existing Python exceptions have `# noqa: C901` on their definitions;
existing Observatory exceptions are listed in `eslint-suppressions.json`. When
refactoring an exception, remove its marker or suppression entry and include a
radon before-and-after complexity table in the pull request. Run
`uv run --locked ruff check --config pyproject.toml --select C901 .` for the
repository-wide Python gate; packages have separate Ruff configurations.

## Maintainer interface

The root `Makefile` is a dev-tooling interface only: `make check`, `make lint`,
`make test`, and the docs targets. There are no root Compose targets — a
repository checkout has no root `compose.yaml`, so `make up` and friends never
worked from the root. Project lifecycle (init, start, stop, logs, service
health) belongs to the `phlo` CLI (`phlo services init|start|stop|logs`,
`phlo doctor`) run inside a Phlo project.

Use the [engineering map](docs/contributing/engineering-map.md) to find source
ownership and generated-file boundaries. `make check` is a broad baseline, not
the exhaustive acceptance suite; the [verification
matrix](docs/contributing/verification-matrix.md) lists the additional checks
for applications, integrations, documentation, workflows, and visible UI work.

## Contributor Licence Agreement

The [Contributor Licence Agreement](CLA.md) describes the additional rights
that may be requested for substantial external contributions. It is not
required unless a Phlo maintainer asks you to complete it.
