# Verification matrix

`make check` is a useful local baseline: it validates support and version metadata, Python linting, formatting, typing, non-integration pytest tests, and Observatory linting, formatting, and typing. It does not build documentation or applications, run frontend or agent tests, run SQL linting, exercise integration services, inspect UI output, audit workflows, or reproduce every CI platform and release gate.

Select checks by changed surface. Run the focused check first, then the broader check for the boundaries touched by the change.

| Change surface | Targeted checks | Broader checks and acceptance |
| --- | --- | --- |
| Documentation | `uvx --from codespell==2.4.1 codespell --config=.codespellrc <changed-files>`; verify changed commands and links | `make docs-build`; inspect the generated page when layout, navigation, code blocks, or rendering changed |
| Core Python and CLI | Focused `uv run --locked pytest <test-path>`; `make typecheck-python`; Ruff on changed Python | `make check`; `make test-core-regression`; `make test-quickstart-smoke` when project generation or lifecycle behaviour changes |
| One Python package | `uv run --locked --package <package> --with-editable ./packages/phlo-testing --with pytest pytest -m "not integration" --tb=short packages/<package>/tests` | Test on Python 3.11 and 3.12 when compatibility matters; `make check`; build the package when metadata or package data changes |
| Integrations and external services | Relevant package or root tests with `pytest -m integration` and required service credentials | Reproduce `.github/workflows/integration.yml` for Iceberg, Dagster, or API boundaries; include container/runtime validation when manifests or images change |
| Observatory Python plugin | Observatory package tests; focused manifest or settings tests | `make check`; package build when bundled frontend files, manifests, or package data change |
| Observatory frontend | From the inner app: `npm test`, `npm run typecheck`, `npm run lint`, `npx prettier --check .`, and `npm run build` | Run the app and inspect affected loading, empty, success, failure, and permission states; capture before/after screenshots for visible changes |
| Phlo GitHub automation | Run `node --experimental-strip-types --test .amp/plugins/phlo-github/lib.test.ts` and `amp plugins list`; from `apps/phlo-github-writer`, run `npm test`, `npm run typecheck`, and `npm run build` | Exercise the affected webhook or Cloudflare Worker boundary; run `npm audit --audit-level=high` for writer dependency changes |
| Schemas, plugin registry, support metadata, or versions | `python3 scripts/validate_support_manifest.py`; `python3 scripts/check_version_drift.py`; relevant JSON validation and focused tests | `make check`; build affected distributions and confirm bundled copies match their authorities |
| Dependencies and lockfiles | Locked install (`uv sync --locked` or `npm ci`); focused tests for the consumer | Relevant audit and build; use CI dependency-risk and security lanes for release or supply-chain changes |
| SQL and dbt | Focused dbt compile/test where available; `make lint-sql` | Integration materialisation against the affected query engine or table store |
| Workflows, scripts, and CI grouping | Focused script tests; `python3 scripts/check_ci_package_groups.py`; parse changed YAML | `make zizmor`; `make actionlint` where Docker is available; exercise workflow-specific dry-run or fixture paths |
| Service manifests, templates, Dockerfiles, or generated Compose | Focused parser/render tests and `docker compose ... config --quiet` on generated output | Quickstart smoke, relevant integration lane, image build, and platform-specific checks such as Windows Compose portability when applicable |
| UI visual behaviour | Component tests plus a production build | Inspect the running UI at representative desktop and narrow widths. Verify interaction, focus, overflow, loading, empty, error, and populated states; attach reviewable screenshots |

## Completion record

Report the commands that passed, the checks skipped because their infrastructure was unavailable, and the boundary each skipped check leaves unverified. A green `make check` alone supports only the baseline described above.
