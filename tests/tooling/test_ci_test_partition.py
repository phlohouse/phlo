"""Contracts for the separately reported core-regression CI partition.

The ``core_regression`` marker must select a nonempty, disjoint subset of the
integration-excluded test suite, and ci.yml must run both partitions with
their own steps.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import yaml

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
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    steps = {
        step["id"]: step for step in workflow["jobs"]["python-core-tests"]["steps"] if "id" in step
    }
    assert shlex.split(steps["core_regression"]["run"]) == ["make", "test-core-regression"]
    assert shlex.split(steps["core_tests"]["run"].replace("${{ matrix.shard }}", "1")) == [
        "make",
        "test-core-shard",
        "CORE_TEST_SHARD_INDEX=1",
    ]
    dry_run = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "--just-print",
            "test-core-shard",
            "CORE_TEST_SHARD_INDEX=1",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    command = shlex.split(dry_run.stdout.replace("\\\n", ""))
    assert command[:7] == ["uv", "run", "--locked", "python", "-m", "pytest", "tests"]
    assert command[7:9] == ["-m", "not integration and not core_regression"]
    assert "--ci-shard-index=1" in command
    assert "--ci-shard-count=3" in command
    assert command[command.index("-n") + 1] == "2"

    core_regression = _collect_node_ids("core_regression")
    remaining = _collect_node_ids("not integration and not core_regression")
    intended = _collect_node_ids("not integration")

    assert core_regression
    assert not core_regression & remaining
    assert core_regression | remaining == intended
