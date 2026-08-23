"""Contract tests for the required PostgreSQL concurrency CI gate.

Pins the ci.yml postgres-concurrency-gates job: its own attributable CI
status gate, a Postgres 16 service, both required test DSNs with driver and
connection checks, exactly the three live concurrency/upgrade selectors,
and that the nightly golden-path schedule remains untouched.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

SERVICE_DSN = "PHLO_SERVICE_TOKEN_TEST_POSTGRES_DSN"
RUN_EVIDENCE_DSN = "PHLO_RUN_EVIDENCE_TEST_POSTGRES_DSN"
SELECTORS = (
    "tests/unit/phlo/security/test_service_identity.py::test_postgres_nonce_store_rejects_one_of_two_simultaneous_consumers",
    "tests/observability/test_run_evidence.py::test_postgres_concurrent_duplicate_replay_does_not_apply_loser_run_metadata",
    "tests/observability/test_run_evidence.py::test_true_v2_to_v3_upgrade_is_idempotent_and_compatible_postgres",
)


def _workflow() -> str:
    return CI_WORKFLOW.read_text(encoding="utf-8")


def _top_level_block(workflow: str, key: str) -> str:
    marker = f"  {key}:\n"
    start = workflow.index(marker) + len(marker)
    remainder = workflow[start:]
    next_key = re.search(r"^  [a-z0-9_-]+:\n", remainder, re.MULTILINE)
    end = next_key.start() if next_key else len(remainder)
    return remainder[:end]


def test_required_postgres_gate_is_separately_attributable_and_gated() -> None:
    workflow = _workflow()
    job = _top_level_block(workflow, "postgres-concurrency-gates")
    ci_status = _top_level_block(workflow, "ci-status")

    assert "name: python / postgres concurrency gates" in job
    assert "image: postgres:16-alpine" in job
    assert "POSTGRES_USER: phlo_ci" in job
    assert "POSTGRES_PASSWORD: phlo_ci_password" in job
    assert "POSTGRES_DB: phlo_ci" in job
    assert "      - postgres-concurrency-gates\n" in ci_status
    assert "POSTGRES_CONCURRENCY_GATES: ${{ needs.postgres-concurrency-gates.result }}" in ci_status
    assert '"${POSTGRES_CONCURRENCY_GATES}" \\' in ci_status


def test_postgres_gate_requires_both_dsns_driver_and_connection() -> None:
    job = _top_level_block(_workflow(), "postgres-concurrency-gates")

    for dsn_name in (SERVICE_DSN, RUN_EVIDENCE_DSN):
        assert 'if [ -z "${!dsn_name:-}" ]' in job
        assert dsn_name in job
    assert "import psycopg2" in job
    assert "psycopg2.connect(os.environ[name], connect_timeout=5)" in job
    assert 'cursor.execute("SELECT 1")' in job


def test_postgres_gate_runs_exactly_the_three_live_selectors() -> None:
    job = _top_level_block(_workflow(), "postgres-concurrency-gates")

    assert "uv run --locked pytest --import-mode=importlib" in job
    for selector in SELECTORS:
        assert job.count(selector) == 1
    assert "PostgreSQL concurrency gate expected exactly 3 passed, 0 skipped" in job
    assert re.search(r"PostgreSQL concurrency gates: \{passed\} passed, \{skipped\} skipped", job)


def test_postgres_gate_does_not_replace_nightly_golden_path_scheduling() -> None:
    nightly = (REPO_ROOT / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")

    assert "release-golden-path:" in nightly
    assert 'cron: "0 3 * * *"' in nightly
    assert "workflow_dispatch:" in nightly
