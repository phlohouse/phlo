"""Contracts for the required parallel integration workflow.

Parses the checked-in workflows instead of mirroring their text: CI must run
the core regression lane through the ``core_regression`` marker filter, keep
the PostgreSQL contract in the required integration caller, and exercise the
documented Postgres lock-contention guard suites under an exact pass-count
guard. Executing the workflow's shell gates verifies that missing producers or
an incorrect transferred image cannot pass. The nightly cron stays unchanged.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"
CI_WORKFLOW = "ci.yml"
PR_WORKFLOW = "pr.yml"
INTEGRATION_WORKFLOW = "integration.yml"
NIGHTLY_WORKFLOW = "nightly.yml"

CORE_REGRESSION_TARGET = "test-core-regression"
CORE_REGRESSION_MARKER = "core_regression"
GATE_JOB = "integration"
OPERATIONS_JOB = "operations"
GATE_STEP = "Run PostgreSQL concurrency gates"
API_GROUPS = ("api-contracts", "api-preview", "api-query-journey", "api-branch-journey")
MINIO_IMAGE = "ghcr.io/phlohouse/phlo-minio:0.17.2"
MINIO_IMAGE_ID = "sha256:" + "a" * 64
GATE_DSN_ENV_VARS = (
    "PHLO_SERVICE_TOKEN_TEST_POSTGRES_DSN",
    "PHLO_RUN_EVIDENCE_TEST_POSTGRES_DSN",
)
EXPECTED_GUARD_PASSES = 3
# Modules carrying the documented Postgres lock-contention guards: the nonce
# compare-and-set store (src/phlo/security/service_identity.py) and the
# serialized parent-run evidence upsert (src/phlo/run_evidence/store.py).
GUARD_TEST_MODULES = (
    "tests/security/test_service_identity.py",
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
    pr = _parse_yaml(WORKFLOW_ROOT / PR_WORKFLOW)
    caller = pr["jobs"][GATE_JOB]
    assert caller.get("uses") == f"./.github/workflows/{INTEGRATION_WORKFLOW}"
    selection = caller.get("if") or ""
    assert "needs.changes.outputs.integration == 'true'" in selection
    assert "needs.changes.outputs.python == 'true'" in selection
    assert "||" in selection, "Python-only changes must also run the integration contracts"

    integration = _parse_yaml(WORKFLOW_ROOT / INTEGRATION_WORKFLOW)
    gate = integration["jobs"][OPERATIONS_JOB]

    postgres_service = (gate.get("services") or {}).get("postgres") or {}
    assert re.fullmatch(
        r"mirror\.gcr\.io/library/postgres:16-alpine@sha256:[0-9a-f]{64}",
        postgres_service.get("image", ""),
    )
    for dsn_name in GATE_DSN_ENV_VARS:
        assert dsn_name in (gate.get("env") or {})

    status = pr["jobs"]["required"]
    assert GATE_JOB in (status.get("needs") or [])
    status_steps = [
        step
        for step in _steps(status)
        if isinstance(step.get("env"), dict) and "INTEGRATION" in step["env"]
    ]
    assert len(status_steps) == 1, "pr / required must map the integration result exactly once"
    assert status_steps[0]["env"]["INTEGRATION"] == f"${{{{ needs.{GATE_JOB}.result }}}}"
    assert 'test "$INTEGRATION" = success' in (status_steps[0].get("run") or "")

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


@pytest.mark.parametrize("job_id", ["contracts", "api", OPERATIONS_JOB])
def test_execution_jobs_share_locked_workspace_setup(job_id: str) -> None:
    integration = _parse_yaml(WORKFLOW_ROOT / INTEGRATION_WORKFLOW)
    job = integration["jobs"][job_id]
    steps = _steps(job)
    assert sum("actions/checkout@" in step.get("uses", "") for step in steps) == 1
    assert _step_named(job, "Sync complete workspace")["run"] == (
        "uv sync --all-packages --dev --locked"
    )
    assert job["env"]["UV_PYTHON"] == "3.12"
    assert not job.get("continue-on-error", False)
    assert all(not step.get("continue-on-error", False) for step in steps)


@pytest.mark.parametrize("job_id", ["contracts", "api"])
def test_named_contract_jobs_require_the_transferred_image_and_preserve_results(
    job_id: str,
) -> None:
    job = _parse_yaml(WORKFLOW_ROOT / INTEGRATION_WORKFLOW)["jobs"][job_id]
    assert job["needs"] == "minio-image"
    assert job["env"]["MINIO_IMAGE"] == MINIO_IMAGE
    download = _step_named(job, "Download the checkout-built MinIO image")
    assert download["with"]["name"] == "integration-minio-image"
    assert download["with"]["path"] == "${{ runner.temp }}"
    load = _step_named(job, "Load and verify the checkout-built MinIO image")
    assert load["id"] == "minio"
    assert load["env"]["EXPECTED_MINIO_ID"] == "${{ needs.minio-image.outputs.image_id }}"

    run = _step_named(job, "Run named behavioral contracts")
    assert "!cancelled()" in run["if"]
    assert "steps.workspace.outcome == 'success'" in run["if"]
    assert "steps.minio.outcome == 'success'" in run["if"]
    group = "storage" if job_id == "contracts" else '"$CONTRACT_GROUP"'
    assert run["run"] == f"uv run --locked python scripts/run_integration.py --group {group}"
    upload = _step_named(job, "Upload required suite results")
    suffix = "storage" if job_id == "contracts" else "${{ matrix.group }}"
    assert upload["if"] == "always()"
    assert upload["with"]["name"] == f"integration-results-{suffix}"
    assert upload["with"]["path"] == "test-results/integration/"
    assert upload["with"]["if-no-files-found"] == "error"
    summary = _step_named(job, "Publish designated suite execution and skip reasons")
    assert summary["if"] == "always()"


def test_operations_keep_independent_guards_and_failure_artifacts() -> None:
    job = _parse_yaml(WORKFLOW_ROOT / INTEGRATION_WORKFLOW)["jobs"][OPERATIONS_JOB]
    assert not job.get("needs"), "operational contracts do not depend on the MinIO source build"
    for name in (
        "Preflight PostgreSQL gate dependencies",
        GATE_STEP,
        "Run quickstart smoke test",
        "Run owned backup and restore drill",
    ):
        step = _step_named(job, name)
        assert "!cancelled()" in step.get("if", "")
        assert "outcome == 'success'" in step["if"]
        assert not step.get("continue-on-error", False)
    uploads = [step for step in _steps(job) if "actions/upload-artifact@" in step.get("uses", "")]
    assert all(step["if"] == "always()" for step in uploads)
    artifacts = {step["with"]["name"]: step["with"] for step in uploads}
    assert artifacts["postgres-concurrency-results"]["path"] == (
        "test-results/postgres-concurrency.xml"
    )
    assert artifacts["postgres-concurrency-results"]["if-no-files-found"] == "error"
    assert artifacts["quickstart-smoke"]["include-hidden-files"] is True
    assert artifacts["quickstart-smoke"]["path"] == "quickstart-artifacts/"
    assert artifacts["quickstart-smoke"]["if-no-files-found"] == "error"
    assert artifacts["recovery-continuity-drill"]["path"] == (
        "recovery-artifacts/recovery-drill.json"
    )
    quickstart = _step_named(job, "Run quickstart smoke test")
    assert quickstart["env"]["COVERAGE_FILE"] == "quickstart-artifacts/.coverage.quickstart"
    assert "-p scripts.ci_required" in quickstart["run"]
    assert "--cov=phlo --cov-report=" in quickstart["run"]


def test_minio_build_preserves_checkout_source_and_exports_one_local_image() -> None:
    jobs = _parse_yaml(WORKFLOW_ROOT / INTEGRATION_WORKFLOW)["jobs"]
    image = jobs["minio-image"]
    assert not image.get("needs")
    assert image["env"]["MINIO_IMAGE"] == MINIO_IMAGE
    builds = [
        (job_id, step)
        for job_id, job in jobs.items()
        for step in _steps(job)
        if step.get("uses") == "./.github/actions/build-minio-image"
    ]
    assert len(builds) == 1
    assert builds[0][0] == "minio-image"
    assert not any(
        "docker/build-push-action@" in step.get("uses", "")
        for job in jobs.values()
        for step in _steps(job)
    ), "MinIO builds must use the shared action instead of a second inline recipe"
    assert builds[0][1]["with"] == {"image": "${{ env.MINIO_IMAGE }}"}
    action = _parse_yaml(REPO_ROOT / ".github/actions/build-minio-image/action.yml")
    assert action["inputs"]["image"]["required"] is True
    assert action["runs"]["using"] == "composite"
    steps = action["runs"]["steps"]
    assert steps[0]["uses"] == "./.github/actions/docker-mirror"
    assert steps[1]["uses"] == (
        "docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069"
    )
    assert 'mirrors = ["mirror.gcr.io"]' in steps[1]["with"]["buildkitd-config-inline"]
    assert steps[2]["uses"] == ("docker/build-push-action@10e90e3645eae34f1e60eeb005ba3a3d33f178e8")
    assert steps[2]["env"]["DOCKER_BUILD_RECORD_UPLOAD"] == "false"
    assert len(steps) == 3
    build = steps[2]["with"]
    assert build["context"] == "packages/phlo-minio/src/phlo_minio"
    assert build["file"] == "packages/phlo-minio/src/phlo_minio/Dockerfile"
    assert build["tags"] == "${{ inputs.image }}"
    assert build["platforms"] == "linux/amd64"
    assert build["load"] is True
    assert build["push"] is False
    assert build["no-cache-filters"] == "runtime"
    assert set(build["cache-from"].splitlines()) == {
        "type=gha,scope=minio-amd64",
        "type=gha,scope=integration-minio-amd64",
    }
    assert "scope=integration-minio-amd64" in build["cache-to"]
    assert "ignore-error=true" in build["cache-to"]
    assert image["outputs"]["image_id"] == "${{ steps.export.outputs.image_id }}"
    upload = _step_named(image, "Upload the checkout-built MinIO image")["with"]
    assert upload["name"] == "integration-minio-image"
    assert upload["path"] == "${{ runner.temp }}/integration-minio.tar"
    assert upload["if-no-files-found"] == "error"
    assert upload["compression-level"] == 0
    assert 0 < upload["retention-days"] <= 3


@pytest.mark.parametrize("job_id", ["contracts", "api"])
@pytest.mark.parametrize(
    ("actual_id", "expected_id", "load_status", "inspect_status", "accepted"),
    [
        (MINIO_IMAGE_ID, MINIO_IMAGE_ID, 0, 0, True),
        ("sha256:" + "b" * 64, MINIO_IMAGE_ID, 0, 0, False),
        (MINIO_IMAGE_ID, MINIO_IMAGE_ID, 7, 0, False),
        (MINIO_IMAGE_ID, MINIO_IMAGE_ID, 0, 7, False),
        (MINIO_IMAGE_ID, "", 0, 0, False),
    ],
)
def test_image_consumer_requires_successful_load_and_exact_image_id(
    tmp_path: Path,
    job_id: str,
    actual_id: str,
    expected_id: str,
    load_status: int,
    inspect_status: int,
    accepted: bool,
) -> None:
    job = _parse_yaml(WORKFLOW_ROOT / INTEGRATION_WORKFLOW)["jobs"][job_id]
    load = _step_named(job, "Load and verify the checkout-built MinIO image")
    docker = tmp_path / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            """\
            import os
            import sys

            args = sys.argv[1:]
            if args[:2] == ["image", "load"]:
                raise SystemExit(int(os.environ["DOCKER_LOAD_STATUS"]))
            if args[:2] == ["image", "inspect"] and args[-1] == os.environ["MINIO_IMAGE"]:
                print(os.environ["ACTUAL_MINIO_ID"])
                raise SystemExit(int(os.environ["DOCKER_INSPECT_STATUS"]))
            raise SystemExit(99)
            """
        ),
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = dict(
        os.environ,
        PATH=f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        RUNNER_TEMP=str(tmp_path),
        MINIO_IMAGE=MINIO_IMAGE,
        EXPECTED_MINIO_ID=expected_id,
        ACTUAL_MINIO_ID=actual_id,
        DOCKER_LOAD_STATUS=str(load_status),
        DOCKER_INSPECT_STATUS=str(inspect_status),
    )
    result = subprocess.run(["bash", "-c", load["run"]], env=env, capture_output=True, timeout=5)
    assert (result.returncode == 0) == accepted, result.stderr.decode()


def test_integration_aggregate_requires_every_job_and_retains_combined_results() -> None:
    jobs = _parse_yaml(WORKFLOW_ROOT / INTEGRATION_WORKFLOW)["jobs"]
    gate = jobs[GATE_JOB]
    assert gate["name"] == "integration / required suites"
    assert gate["if"] == "always()"
    assert set(gate["needs"]) == set(jobs) - {GATE_JOB}
    assert jobs["api"]["strategy"]["fail-fast"] is False
    assert jobs["api"]["strategy"]["matrix"] == {"group": list(API_GROUPS)}
    assert jobs["api"]["env"]["CONTRACT_GROUP"] == "${{ matrix.group }}"
    producers = _step_named(gate, "Require every integration producer")
    assert producers["env"] == {
        "MINIO_IMAGE": "${{ needs.minio-image.result }}",
        "CONTRACTS": "${{ needs.contracts.result }}",
        "API": "${{ needs.api.result }}",
        "OPERATIONS": "${{ needs.operations.result }}",
    }
    assert _steps(gate)[0] == producers
    assert all(not step.get("continue-on-error", False) for step in _steps(gate))
    download = _step_named(gate, "Collect every named contract report")
    assert download["with"]["pattern"] == "integration-results-*"
    assert download["with"]["path"] == "test-results/integration/"
    assert download["with"]["merge-multiple"] is True
    assert "if" not in download, (
        "combined reports are collected only after the producer gate passes"
    )
    upload = _step_named(gate, "Upload required suite results")
    assert upload["with"]["name"] == "integration-results"
    assert upload["with"]["path"] == "test-results/integration/"
    assert upload["with"]["if-no-files-found"] == "error"


@pytest.mark.parametrize("producer", ["MINIO_IMAGE", "CONTRACTS", "API", "OPERATIONS"])
@pytest.mark.parametrize("outcome", ["failure", "cancelled", "skipped", "", None])
def test_integration_aggregate_rejects_any_unsuccessful_producer(
    producer: str, outcome: str | None
) -> None:
    job = _parse_yaml(WORKFLOW_ROOT / INTEGRATION_WORKFLOW)["jobs"][GATE_JOB]
    gate = _step_named(job, "Require every integration producer")
    env = dict(os.environ, **dict.fromkeys(gate["env"], "success"))
    if outcome is None:
        env.pop(producer)
    else:
        env[producer] = outcome
    result = subprocess.run(["bash", "-c", gate["run"]], env=env, capture_output=True, timeout=5)
    assert result.returncode != 0, f"accepted {producer}={outcome!r}"


def test_integration_aggregate_accepts_all_successful_producers() -> None:
    job = _parse_yaml(WORKFLOW_ROOT / INTEGRATION_WORKFLOW)["jobs"][GATE_JOB]
    gate = _step_named(job, "Require every integration producer")
    env = dict(os.environ, **dict.fromkeys(gate["env"], "success"))
    result = subprocess.run(["bash", "-c", gate["run"]], env=env, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr.decode()


def test_nightly_keeps_its_scheduled_cron_trigger() -> None:
    nightly = _parse_yaml(WORKFLOW_ROOT / NIGHTLY_WORKFLOW)
    triggers = nightly.get(True) or nightly.get("on") or {}

    schedules = [entry.get("cron") for entry in triggers.get("schedule") or []]
    assert NIGHTLY_CRON in schedules, "nightly must keep its 03:00 UTC schedule"
    assert "workflow_dispatch" in triggers
