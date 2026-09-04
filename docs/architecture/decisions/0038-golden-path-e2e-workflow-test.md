# ADR 0038: Golden Path E2E Workflow Test

## Status

**Accepted**

## Context

We need a repeatable, end-to-end verification of the default Phlo experience: project scaffolding,
service orchestration, ingestion, transformation, publishing, and optional service wiring.
Earlier investigations introduced a Trino-specific diagnostic test to pinpoint startup issues, but
that approach duplicates responsibility and fragments the system-level workflow story.

The E2E test should:
- exercise the CLI as users do,
- run against a fresh environment (including Docker services),
- validate the resulting artifacts and data flows,
- provide actionable logs for each step, and
- support both development installs and PyPI installs.

## Decision

Adopt a single golden-path E2E workflow test (pytest-based) that:
- initializes a project into a temp directory,
- validates scaffolded files,
- uses the CLI to create and run an ingestion workflow against JSONPlaceholder,
- runs dbt transforms, publishes marts to Postgres, and verifies row counts,
- starts core services with Docker Compose and waits for readiness, and
- incrementally adds optional services (e.g., observability stack) and asserts auto-wiring.

Remove the standalone Trino startup diagnostic test in favor of the integrated E2E flow.

## Implementation

- Use `tests/test_golden_path_e2e.py` as the single golden-path E2E test.
- Write per-step logs to a temporary directory for debugging.
- Support `PHLO_E2E_MODE=dev|pypi` to select local source vs PyPI install paths.
- Gate execution behind `PHLO_E2E=1` to avoid accidental runs.
- Remove `tests/test_trino_startup_diagnostics.py`.

## Consequences

### Positive

- End-to-end confidence in the default user workflow.
- CLI-driven verification matches real usage patterns.
- Per-step logs make failures actionable without pytest output noise.
- One canonical test reduces duplication and drift.

### Negative

- Longer runtime than unit or targeted integration tests.
- Requires Docker and external network access for data ingestion.

## Verification

- Run `PHLO_E2E=1 PHLO_E2E_MODE=dev uv run pytest tests/test_golden_path_e2e.py -m integration -s`.
- Inspect step logs in the generated `e2e-logs` directory when failures occur.
