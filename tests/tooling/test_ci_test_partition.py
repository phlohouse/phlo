"""Contracts for the separately reported core-regression CI partition.

The ``core_regression`` marker must select a nonempty, disjoint subset of the
integration-excluded test suite, and ci.yml must run both partitions with
their own steps.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _collect_node_ids(marker: str) -> set[str]:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "--quiet",
            "--import-mode=importlib",
            "tests",
            "-m",
            marker,
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {
        line.strip()
        for line in result.stdout.splitlines()
        if line.startswith("tests/") and "::" in line
    }


def test_ci_keeps_core_regression_attributable_and_disjoint() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "id: core_regression" in workflow
    assert "make test-core-regression" in workflow
    assert "id: core_tests" in workflow
    assert (
        'uv run --locked python -m pytest tests -m "not integration and not core_regression"'
        in workflow
    )

    core_regression = _collect_node_ids("core_regression")
    remaining = _collect_node_ids("not integration and not core_regression")
    intended = _collect_node_ids("not integration")

    assert core_regression
    assert not core_regression & remaining
    assert core_regression | remaining == intended
