"""Keep nightly maintenance separate from immutable candidate acceptance."""

import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"


def test_nightly_maintenance_does_not_claim_artifact_qualification(tmp_path: Path) -> None:
    ci = yaml.safe_load((WORKFLOW_ROOT / "ci.yml").read_text(encoding="utf-8"))
    nightly = yaml.safe_load((WORKFLOW_ROOT / "nightly.yml").read_text(encoding="utf-8"))
    assert "release-golden-path" not in ci["jobs"]
    report = nightly["jobs"]["release-artifact-acceptance"]
    output = tmp_path / "summary"
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", report["steps"][0]["run"]],
        env={"GITHUB_STEP_SUMMARY": str(output)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert output.read_text().splitlines() == [
        "Artifact qualification: artifact-not-staged. Maintenance does not stage a release candidate.",
        "Use Release Stage and Release Artifact Acceptance for immutable candidate qualification.",
    ]
    summary = nightly["jobs"]["nightly-status"]
    assert "upstream-compatibility" in summary["needs"]


def test_windows_portability_runs_release_contracts_in_an_isolated_environment() -> None:
    workflow = yaml.safe_load(
        (WORKFLOW_ROOT / "windows-compose-portability.yml").read_text(encoding="utf-8")
    )
    assert "concurrency" not in workflow
    ci = yaml.safe_load((WORKFLOW_ROOT / "ci.yml").read_text(encoding="utf-8"))["jobs"]
    assert ci["windows-portability"]["needs"] == "ci-config"
    assert ci["windows-portability"]["with"]["reuse-wheelhouse"] is True
    preparation = workflow["jobs"]["generate-linux-fixture"]["steps"]
    download = next(
        step for step in preparation if step.get("name") == "Download the validation wheelhouse"
    )
    assert download["if"] == "inputs.reuse-wheelhouse"
    assert download["with"]["name"] == "provider-wheelhouse"
    build = next(
        step for step in preparation if step.get("name") == "Build installed CLI and service wheels"
    )
    assert build["if"] == "${{ !inputs.reuse-wheelhouse }}"
    job = workflow["jobs"]["verify-windows-fixture"]
    assert job["runs-on"] == "windows-latest"
    steps = {step["name"]: step for step in job["steps"] if "name" in step}
    assert steps["Set up Python 3.12"]["with"]["python-version"] == "3.12"
    install = steps["Install focused Python test dependency"]["run"]
    assert '"phlo-windows-harness-venv"' in install
    assert "uv venv --python 3.12 $venv" in install
    assert "uv pip install --python $python pytest" in install
    harness = steps["Run focused Python harness tests"]
    assert harness["shell"] == "pwsh"
    assert harness["run"] == (
        "& $env:HARNESS_PYTHON -m pytest tests/scripts/test_release_golden_path.py "
        "--tb=short --junitxml=test-results/windows.xml"
    )
    launcher = steps["Run PowerShell launcher contract tests"]
    assert launcher["shell"] == "pwsh"
    assert launcher["run"] == "./tests/scripts/test_release_golden_path.ps1"
    names = list(steps)
    assert (
        names.index("Set up Python 3.12")
        < names.index("Install focused Python test dependency")
        < names.index("Run focused Python harness tests")
        < names.index("Run PowerShell launcher contract tests")
    )


@pytest.mark.parametrize(
    ("overrides", "exit_code", "message"),
    [
        ({}, 0, ""),
        ({"UPSTREAM_COMPATIBILITY": "failure"}, 1, "Scheduled fresh resolution failed: failure"),
        ({"EVENT_NAME": "workflow_dispatch", "UPSTREAM_COMPATIBILITY": "skipped"}, 0, ""),
        (
            {"FULL_INTEGRATION": "failure"},
            1,
            "Required nightly check finished with result: failure",
        ),
        (
            {"RELEASE_GOLDEN_PATH": "cancelled"},
            1,
            "Required nightly check finished with result: cancelled",
        ),
        (
            {"OPERATIONS_EVIDENCE": "skipped"},
            1,
            "Required nightly check finished with result: skipped",
        ),
        ({"RELEASE_ARTIFACT_ACCEPTANCE": "failure"}, 1, "Staging outcome report failed: failure"),
    ],
)
def test_nightly_status_reports_and_propagates_results(tmp_path, overrides, exit_code, message):
    nightly = yaml.safe_load((WORKFLOW_ROOT / "nightly.yml").read_text())
    step = nightly["jobs"]["nightly-status"]["steps"][0]
    output = tmp_path / "summary"
    env = dict.fromkeys(step["env"], "success")
    env.update(EVENT_NAME="schedule", GITHUB_STEP_SUMMARY=str(output))
    env.update(overrides)
    result = subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True, text=True)
    assert result.returncode == exit_code
    assert result.stdout.strip() == (f"::error::{message}" if message else "")
    assert (
        output.read_text().splitlines()[0]
        == f"Fresh dependency resolution: {env['UPSTREAM_COMPATIBILITY']}"
    )
    if exit_code == 0:
        assert (
            output.read_text().splitlines()[-1]
            == "Artifact qualification: artifact-not-staged (not release evidence)."
        )


def test_nightly_release_golden_path_keeps_dispatch_and_schedule() -> None:
    nightly = yaml.safe_load((WORKFLOW_ROOT / "nightly.yml").read_text(encoding="utf-8"))
    triggers = nightly.get("on") or nightly[True]

    assert "workflow_dispatch" in triggers
    assert {"cron": "0 3 * * *"} in triggers["schedule"]


def test_nightly_minio_cache_uses_the_shared_build_only_on_trusted_main() -> None:
    nightly = yaml.safe_load((WORKFLOW_ROOT / "nightly.yml").read_text())
    job = nightly["jobs"]["minio-cache"]
    assert job["if"] == (
        "github.repository == 'phlohouse/phlo' && "
        "github.ref == 'refs/heads/main' && "
        "(github.event_name == 'schedule' || github.event_name == 'workflow_dispatch')"
    )
    assert job["permissions"] == {"contents": "read"}
    assert job["continue-on-error"] is True
    assert not job.get("needs")
    checkout, build = job["steps"]
    assert checkout["uses"] == ("actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd")
    assert checkout["with"] == {"persist-credentials": False}
    assert build["uses"] == "./.github/actions/build-minio-image"
    assert build["with"] == {"image": "ghcr.io/phlohouse/phlo-minio:0.17.1"}
    assert job["outputs"]["result"] == "${{ steps.build.outcome }}"
    assert build["id"] == "build"
    summary = nightly["jobs"]["nightly-status"]
    assert "minio-cache" in summary["needs"]
    assert summary["steps"][0]["env"]["MINIO_CACHE"] == (
        "${{ needs.minio-cache.outputs.result || needs.minio-cache.result }}"
    )


@pytest.mark.parametrize("cache_result", ["success", "failure", "cancelled", "skipped", ""])
def test_optional_minio_cache_result_is_reported_without_replacing_nightly_gates(
    tmp_path, cache_result
) -> None:
    nightly = yaml.safe_load((WORKFLOW_ROOT / "nightly.yml").read_text())
    step = nightly["jobs"]["nightly-status"]["steps"][0]
    for correctness_result in ("success", "failure"):
        summary = tmp_path / f"summary-{correctness_result}"
        env = dict.fromkeys(step["env"], "success")
        env.update(
            EVENT_NAME="schedule",
            MINIO_CACHE=cache_result,
            FULL_INTEGRATION=correctness_result,
            GITHUB_STEP_SUMMARY=str(summary),
        )
        result = subprocess.run(
            ["bash", "-c", step["run"]], env=env, capture_output=True, text=True
        )
        assert (result.returncode == 0) == (correctness_result == "success")
        assert (
            f"Optional MinIO cache build: {cache_result} "
            "(cache export is best effort; not qualification evidence)."
        ) in summary.read_text().splitlines()


def test_artifact_acceptance_requires_authenticated_staging_without_missing_bom_escape() -> None:
    workflow = yaml.safe_load(
        (WORKFLOW_ROOT / "release-artifact-acceptance.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["acceptance"]["steps"]
    scripts = [step for step in steps if "run" in step]
    assert shlex.split(scripts[0]["run"]) == [
        "python3",
        "scripts/release_provenance.py",
        "collect",
        "--candidate-sha",
        "$CANDIDATE_SHA",
        "--stage-run",
        "$STAGE_RUN",
        "--output",
        "inputs",
    ]
    assert shlex.split(scripts[1]["run"]) == [
        "python3",
        "scripts/release_provenance.py",
        "collect",
        "--candidate-sha",
        "$PREVIOUS_CANDIDATE_SHA",
        "--stage-run",
        "$PREVIOUS_STAGE_RUN",
        "--output",
        "previous-inputs",
    ]
    acceptance = next(step for step in scripts if "scripts/release_golden_path.py" in step["run"])
    assert shlex.split(acceptance["run"]) == [
        "python3",
        "scripts/release_golden_path.py",
        "--candidate-bom",
        "inputs/candidate-$CANDIDATE_SHA/bom.json",
        "--previous-candidate-bom",
        "previous-inputs/candidate-$PREVIOUS_CANDIDATE_SHA/bom.json",
        "--evidence-output",
        "$RUNNER_TEMP/evidence.json",
    ]
    assert all("if" not in step for step in scripts)
    assert steps[-1]["with"]["if-no-files-found"] == "error"
