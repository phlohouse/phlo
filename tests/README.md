# Test layout

Tests are grouped **domain-first**, and package-local wherever the behaviour
belongs to one package.

```text
tests/                     cross-package behaviour only
  audit/ capabilities/ cli/ compliance/ data/ dataset/ governance/
  observability/ operations/ plugins/ runtime/ scripts/ security/ tooling/
  contracts/               cross-package contract harnesses (integration-marked)
  integration/             cross-package end-to-end tests needing a live stack
  fixtures/ goldens/       test data, not code
packages/<package>/tests/  behaviour of that package
```

## Where a test belongs

- If it imports only one `phlo_*` package, it belongs in that package's
  `tests/` directory.
- If it crosses package boundaries (CLI journeys, security manifests, release
  tooling, docs examples), it belongs in the matching `tests/<domain>/`.
- If it needs Docker, a live service, a subprocess toolchain, or a full stack,
  it carries `pytest.mark.integration` and lives either in the package's tests
  or in `tests/integration/<domain>/`.
- Contract harnesses that assert a stack or profile boundary live in
  `tests/contracts/`.

## Markers

- `integration` — needs external services, subprocess tooling, or a full stack.
  Excluded from the default lane by `addopts = -m 'not integration'`. Pure tests
  must not carry it: that silently removes them from normal development runs.
- `core_regression` — small, high-signal gate wired through
  `CORE_REGRESSION_TEST_PATHS` in the `Makefile`.

There is no `unit` marker. The default lane already means "everything except
integration", so the marker added no information and was applied to one file.

## Conventions

- Shared test-support functions live in `tests/support.py`; import them as
  `from tests.support import ...`. Fixtures belong in `conftest.py` when they
  are shared by a directory.
- Name tests after the behaviour they prove, not the score (`test_reclaim_...`,
  not `test_reclaim_works`).
- Keep golden and fixture payloads in `tests/goldens/` and `tests/fixtures/`.

## Running

```bash
make test                                 # parallel, one worker per core, work-stealing
make test PYTEST_WORKERS=4                # pinned worker count
uv run --locked pytest -n0                # serial, for debugging a failure
uv run --locked pytest -m integration     # services / full stacks (serial by default)
uv run --locked pytest -m core_regression # release gate
```

`make test` runs with `--dist worksteal` so slow modules do not leave a tail on
one worker. Each xdist worker is a separate process; tests must not share files
or global state across processes. Use `tmp_path`, per-test stores, and
`monkeypatch` — the API suite pins an isolated run-evidence SQLite file per
test for exactly this reason.

The 300-second per-test timeout is a hang guard, not a performance budget. If a
test legitimately needs longer, mark it `integration` or raise the timeout at
the call site rather than removing the guard.
