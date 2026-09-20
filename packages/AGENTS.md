# Package work

Use the package's `pyproject.toml`, source tree, tests, and README as its local contract. Use the root `pyproject.toml` for workspace dependency resolution and `.github/workflows/ci.yml` for the authoritative CI package groups.

## Route the change

- Put provider behaviour in `packages/<distribution>/src/<module>/` and its focused tests in `packages/<distribution>/tests/`.
- Keep cross-provider orchestration and public CLI behaviour in `src/phlo/`; introduce a core dependency only when the abstraction belongs to every provider.
- Treat service manifests, templates, and Dockerfiles as runtime code. Verify rendered or installed behaviour as well as parsing.
- For Observatory work, follow `phlo-observatory/AGENTS.md`.
- When package metadata, compatibility, or support tier changes, inspect `registry/support/v1.json`, both plugin registry copies, root dependency groups, and lockfiles. Follow the generated-file ownership in `docs/contributing/engineering-map.md`.

## Complete the change

A package change is complete when:

1. The package's focused non-integration tests pass with its workspace environment, for example `uv run --locked --package <package> --with-editable ./packages/phlo-testing --with pytest pytest -m "not integration" --tb=short packages/<package>/tests`.
2. Relevant integration tests pass when the change crosses a service, database, container, or provider boundary.
3. `python3 scripts/check_ci_package_groups.py`, `python3 scripts/check_version_drift.py`, and `python3 scripts/validate_support_manifest.py` pass when their governed metadata changes.
4. The broader checks selected from `docs/contributing/verification-matrix.md` pass, and generated artefacts are either regenerated from their authority or left untouched.
