# Contributing to Phlo

Thank you for contributing to Phlo. Please discuss substantial changes in an
issue before opening a pull request, and run `make check` locally before
submitting it.

## Contribution licence

By submitting a contribution, you confirm that you have the right to submit it
and agree to license it under AGPL-3.0-or-later.

For substantial external contributions, we may ask you to sign our Contributor
Licence Agreement so that Phlo can also offer commercial licences.

## Comments and docstrings

Phlo follows a concise, technically direct commenting style inspired by the
Redis source tree: comments explain why, contracts state guarantees, and
obvious code stays uncommented. The full style guide lives in the
`redis-style-code-comments` agent skill; the binding rules are below.

### Module headers (enforced)

Every tracked `.py`, `.ts`, `.tsx`, `.js`, `.sql`, and `.sh` file starts with
a top-of-file block. For Python this is a module docstring as the first
statement; for TypeScript a `/** ... */` block; for SQL `--` lines; for shell
a `#` block directly after the shebang.

- One sentence of purpose, then only facts evident from the code: contracts,
  invariants, ownership, ordering or failure semantics. Never invent intent.
- Two to six lines for substantive modules; one honest line for thin ones.
- Vendored or generated files may opt out with `phlo: no-header` on line 1.

The `check-file-headers` pre-commit hook enforces presence on changed files.

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

## Maintainer interface

The root `Makefile` is a dev-tooling interface only: `make check`, `make lint`,
`make test`, and the docs targets. There are no root Compose targets — a
repository checkout has no root `compose.yaml`, so `make up` and friends never
worked from the root. Project lifecycle (init, start, stop, logs, service
health) belongs to the `phlo` CLI (`phlo services init|start|stop|logs`,
`phlo doctor`) run inside a Phlo project.

## Contributor Licence Agreement

The [Contributor Licence Agreement](CLA.md) describes the additional rights
that may be requested for substantial external contributions. It is not
required unless a Phlo maintainer asks you to complete it.
