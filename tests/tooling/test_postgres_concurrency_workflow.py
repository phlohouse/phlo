"""Structural contracts for the required PostgreSQL concurrency CI gate.

Parses the checked-in workflows instead of mirroring their text: CI must run
the core regression lane through the ``core_regression`` marker filter, keep
``postgres-concurrency-gates`` as a separately attributable required gate
whose pytest invocation exercises the documented Postgres lock-contention
guard suites under an exact pass-count guard, and keep the nightly cron
trigger untouched.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"
CI_WORKFLOW = "ci.yml"
NIGHTLY_WORKFLOW = "nightly.yml"

CORE_REGRESSION_TARGET = "test-core-regression"
CORE_REGRESSION_MARKER = "core_regression"
GATE_JOB = "postgres-concurrency-gates"
GATE_STEP = "Run PostgreSQL concurrency gates"
GATE_DSN_ENV_VARS = (
    "PHLO_SERVICE_TOKEN_TEST_POSTGRES_DSN",
    "PHLO_RUN_EVIDENCE_TEST_POSTGRES_DSN",
)
EXPECTED_GUARD_PASSES = 3
# Modules carrying the documented Postgres lock-contention guards: the nonce
# compare-and-set store (src/phlo/security/service_identity.py) and the
# serialized parent-run evidence upsert (src/phlo/run_evidence/store.py).
GUARD_TEST_MODULES = (
    "tests/unit/phlo/security/test_service_identity.py",
    "tests/observability/test_run_evidence.py",
)
NIGHTLY_CRON = "0 3 * * *"


def _parse_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in job.get("steps") or [] if isinstance(step, dict)]


def _step_named(job: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [step for step in _steps(job) if step.get("name") == name]
    assert len(matches) == 1, f"expected exactly one step named {name!r}"
    return matches[0]


def _make_recipe(target: str) -> str:
    """Return the recipe body of a Makefile target."""
    lines = (REPO_ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith(f"{target}:")]
    assert len(starts) == 1, f"expected exactly one {target} target in the Makefile"
    recipe: list[str] = []
    for line in lines[starts[0] + 1 :]:
        if not line.startswith("\t"):
            break
        recipe.append(line.removeprefix("\t"))
    return "\n".join(recipe)


def test_core_regression_lane_selects_tests_through_the_marker_filter() -> None:
    ci = _parse_yaml(WORKFLOW_ROOT / CI_WORKFLOW)
    job = ci["jobs"]["python-core-tests"]

    regression_step = _step_named(job, "Run core regression suite")
    assert regression_step.get("id") == "core_regression"
    assert regression_step.get("run") == f"make {CORE_REGRESSION_TARGET}"

    recipe_tokens = _make_recipe(CORE_REGRESSION_TARGET).split()
    marker_indexes = [index for index, token in enumerate(recipe_tokens) if token == "-m"]
    assert any(recipe_tokens[index + 1] == CORE_REGRESSION_MARKER for index in marker_indexes), (
        f"{CORE_REGRESSION_TARGET} must select tests via -m {CORE_REGRESSION_MARKER}"
    )

    general_run = _step_named(job, "Run core tests").get("run") or ""
    marker_expression = re.search(r"""-m\s+(?P<quote>['"])(?P<expr>.*?)(?P=quote)""", general_run)
    assert marker_expression, "general core lane must filter with a quoted -m expression"
    expression_tokens = marker_expression.group("expr").split()
    assert any(
        expression_tokens[index] == "not" and expression_tokens[index + 1] == CORE_REGRESSION_MARKER
        for index in range(len(expression_tokens) - 1)
    ), "general core lane must exclude the core_regression marker"


def test_postgres_gate_is_required_and_runs_the_lock_guard_suites() -> None:
    ci = _parse_yaml(WORKFLOW_ROOT / CI_WORKFLOW)
    jobs = ci["jobs"]
    assert GATE_JOB in jobs, "postgres-concurrency-gates job must exist"
    gate = jobs[GATE_JOB]

    postgres_service = (gate.get("services") or {}).get("postgres") or {}
    assert postgres_service.get("image") == "postgres:16-alpine"
    for dsn_name in GATE_DSN_ENV_VARS:
        assert dsn_name in (gate.get("env") or {})

    status = jobs["ci-status"]
    assert GATE_JOB in (status.get("needs") or [])
    status_steps = [
        step
        for step in _steps(status)
        if isinstance(step.get("env"), dict) and "POSTGRES_CONCURRENCY_GATES" in step["env"]
    ]
    assert len(status_steps) == 1, "ci-status must map the gate result exactly once"
    assert status_steps[0]["env"]["POSTGRES_CONCURRENCY_GATES"] == (
        f"${{{{ needs.{GATE_JOB}.result }}}}"
    )
    assert '"${POSTGRES_CONCURRENCY_GATES}"' in (status_steps[0].get("run") or "")

    gate_script = _step_named(gate, GATE_STEP).get("run") or ""
    assert "uv run --locked pytest" in gate_script
    assert "--import-mode=importlib" in gate_script
    for module in GUARD_TEST_MODULES:
        assert gate_script.count(module) >= 1, f"gate must run {module}"
    assert sum(gate_script.count(module) for module in GUARD_TEST_MODULES) == (
        EXPECTED_GUARD_PASSES
    ), "each selector must map to one expected passing guard test"
    assert re.search(rf"passed\s*!=\s*{EXPECTED_GUARD_PASSES}\b", gate_script), (
        "gate must hard-fail unless every guard test passed"
    )
    assert re.search(r"skipped\s*!=\s*0\b", gate_script), "gate must reject skipped guards"


def test_nightly_keeps_its_scheduled_cron_trigger() -> None:
    nightly = _parse_yaml(WORKFLOW_ROOT / NIGHTLY_WORKFLOW)
    triggers = nightly.get(True) or nightly.get("on") or {}

    schedules = [entry.get("cron") for entry in triggers.get("schedule") or []]
    assert NIGHTLY_CRON in schedules, "nightly must keep its 03:00 UTC schedule"
    assert "workflow_dispatch" in triggers
