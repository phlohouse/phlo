"""Contracts for the exact-SHA release-candidate gate.

Reads workflow and ruleset files as data and asserts the gate holds: the
candidate aggregates every release-critical lane, reusable workflows cannot
cancel each other, release tags bind to the exact candidate SHA, and manual
publishing cannot bypass identity checks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"
GATE_CHECK_NAME = "release candidate / status"
IDENTITY_SCRIPT = REPO_ROOT / "scripts" / "release_identity.py"


def _step_run_script(step: dict[str, Any]) -> str:
    run = step.get("run")
    return run if isinstance(run, str) else ""


def _candidate_gate_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Run steps that block until the aggregate candidate check concludes."""
    return [
        step
        for step in job.get("steps", [])
        if GATE_CHECK_NAME in _step_run_script(step) and "check-runs" in _step_run_script(step)
    ]


def test_candidate_status_fails_closed_on_every_release_critical_lane() -> None:
    candidate = yaml.safe_load((WORKFLOW_ROOT / "release-candidate.yml").read_text())

    triggers = candidate.get("on") or candidate[True]
    assert "pull_request" not in triggers
    assert triggers["push"]["branches"] == ["main", "beta"]
    assert "merge_group" not in triggers
    assert candidate["jobs"]["status"]["name"] == "release candidate / status"
    assert candidate["jobs"]["status"]["if"] == "always()"
    assert candidate["jobs"]["status"]["needs"] == ["ci", "integration", "security", "nightly"]
    upload = next(
        step
        for step in candidate["jobs"]["status"]["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert upload["with"]["name"] == "release-candidate-evidence-${{ github.sha }}"
    assert upload["with"]["if-no-files-found"] == "error"


def test_reusable_evidence_workflows_do_not_cancel_one_another() -> None:
    for name in ("ci.yml", "integration.yml", "security.yml", "nightly.yml"):
        workflow = yaml.safe_load((WORKFLOW_ROOT / name).read_text())
        assert "workflow_call" in (workflow.get("on") or workflow[True])
        assert "concurrency" not in workflow


def test_candidate_passes_only_required_service_secrets_to_reusable_workflows() -> None:
    candidate = yaml.safe_load((WORKFLOW_ROOT / "release-candidate.yml").read_text())

    expected = {
        "POSTGRES_PASSWORD": "${{ secrets.POSTGRES_PASSWORD }}",
        "MINIO_ROOT_PASSWORD": "${{ secrets.MINIO_ROOT_PASSWORD }}",
        "SUPERSET_ADMIN_PASSWORD": "${{ secrets.SUPERSET_ADMIN_PASSWORD }}",
    }
    assert candidate["jobs"]["integration"]["secrets"] == expected
    assert candidate["jobs"]["nightly"]["secrets"] == expected
    assert "secrets" not in candidate["jobs"]["ci"]
    assert "secrets" not in candidate["jobs"]["security"]


def test_ci_status_includes_every_installed_provider_artifact_shard() -> None:
    ci = yaml.safe_load((WORKFLOW_ROOT / "ci.yml").read_text())

    assert ci["jobs"]["installed-provider-artifacts"]["strategy"]["matrix"]["docker-shard"] == [
        0,
        1,
        2,
        3,
    ]
    assert "installed-provider-artifacts" in ci["jobs"]["ci-status"]["needs"]
    summary = ci["jobs"]["ci-status"]["steps"][0]
    assert (
        summary["env"]["INSTALLED_PROVIDER_ARTIFACTS"]
        == "${{ needs.installed-provider-artifacts.result }}"
    )


def test_release_tag_requires_successful_aggregate_for_its_exact_sha() -> None:
    release = yaml.safe_load((WORKFLOW_ROOT / "release.yml").read_text())
    tag_job = release["jobs"]["release-tag"]

    assert tag_job["permissions"]["checks"] == "read"

    gates = _candidate_gate_steps(tag_job)
    assert len(gates) == 1
    gate = gates[0]
    assert gate is tag_job["steps"][0]  # the gate runs before any side effect
    assert gate["env"] == {
        "GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}",
        "CANDIDATE_SHA": "${{ github.sha }}",
        "REPOSITORY": "${{ github.repository }}",
    }
    assert gate["shell"] == "bash"
    assert gate["run"].lstrip().startswith("set -euo pipefail")
    assert gate["run"].count("exit 1") >= 2  # failed conclusion and timeout abort
    assert "if" not in gate
    assert "continue-on-error" not in gate
    assert not tag_job.get("continue-on-error")

    identity = next(step for step in tag_job["steps"] if step.get("id") == "release-identity")
    assert identity["env"]["CANDIDATE_SHA"] == "${{ github.sha }}"
    assert identity["env"]["RELEASE_BRANCH"] == "${{ github.ref_name }}"
    assert "scripts/release_identity.py source" in _step_run_script(identity)
    assert IDENTITY_SCRIPT.is_file()

    verify = next(
        step
        for step in tag_job["steps"]
        if (step.get("env") or {}).get("TAG") == "${{ steps.release-identity.outputs.tag }}"
    )
    assert verify["env"]["CANDIDATE_SHA"] == "${{ github.sha }}"


def test_release_publish_validates_the_complete_artifact_manifest() -> None:
    release = yaml.safe_load((WORKFLOW_ROOT / "release.yml").read_text())
    publish_job = release["jobs"]["publish"]

    gates = _candidate_gate_steps(publish_job)
    assert len(gates) == 1
    gate = gates[0]
    assert set(gate["env"]) == {"GH_TOKEN", "REPOSITORY"}
    assert gate["run"].lstrip().startswith("set -euo pipefail")
    assert "git rev-parse HEAD" in gate["run"]  # SHA is derived from the tag target
    assert "if" not in gate
    assert "continue-on-error" not in gate
    assert not publish_job.get("continue-on-error")

    commands = [
        line.split()
        for step in publish_job["steps"]
        for line in _step_run_script(step).splitlines()
    ]
    subcommands = {
        parts[2]
        for parts in commands
        if len(parts) > 2 and parts[:2] == ["python", "scripts/release_identity.py"]
    }
    assert {"artifacts", "publish-plan"} <= subcommands
    assert IDENTITY_SCRIPT.is_file()


def test_manual_artifact_workflow_cannot_bypass_release_identity_checks() -> None:
    workflow = (WORKFLOW_ROOT / "publish.yml").read_text()

    assert "uv publish" not in workflow
    assert "name: Build Package Artifacts" in workflow


def test_versioned_ruleset_requires_review_and_candidate_status() -> None:
    ruleset = json.loads((REPO_ROOT / "security/release-candidate-ruleset.json").read_text())

    assert ruleset["enforcement"] == "active"
    assert ruleset["conditions"]["ref_name"]["include"] == ["refs/heads/main", "refs/heads/beta"]
    assert ruleset["bypass_actors"] == []
    rule_types = {rule["type"] for rule in ruleset["rules"]}
    assert {"pull_request", "required_status_checks"} <= rule_types
    assert "pr / required" in (REPO_ROOT / "security/release-candidate-ruleset.json").read_text()
