"""Source health and immutable release readiness are separate fail-closed contracts."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = REPO_ROOT / ".github/workflows"


def workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOW_ROOT / name).read_text())


def test_main_health_reuses_full_queue_or_calls_the_full_merge_contract():
    candidate = workflow("release-candidate.yml")
    events = candidate.get("on") or candidate[True]
    assert events["push"]["branches"] == ["main", "beta"]
    assert not {"merge_group", "pull_request"} & set(events)
    jobs = candidate["jobs"]
    assert jobs["status"]["name"] == "release candidate / status"
    assert jobs["status"]["needs"] == ["reuse", "full"]
    assert jobs["full"]["uses"] == "./.github/workflows/pr.yml"
    assert jobs["reuse"]["outputs"]["reused"] == "${{ steps.evidence.outputs.reused }}"
    assert jobs["full"]["if"] == "always() && needs.reuse.outputs.reused != 'true'"
    assert "vars." not in str(jobs["reuse"])
    assert "nightly" not in jobs
    assert candidate["permissions"] == {
        "contents": "read",
        "actions": "read",
        "pull-requests": "read",
    }
    assert all("secrets" not in job for job in jobs.values())


@pytest.mark.parametrize(
    "reused,reuse,full,accepted",
    [
        ("true", "success", "skipped", True),
        ("true", "failure", "skipped", False),
        ("true", "success", "success", False),
        ("false", "success", "success", True),
        ("", "failure", "success", True),
        ("false", "success", "skipped", False),
        ("false", "success", "failure", False),
        ("false", "success", "cancelled", False),
    ],
)
def test_source_health_gate_executes_fail_closed(tmp_path, reused, reuse, full, accepted):
    script = workflow("release-candidate.yml")["jobs"]["status"]["steps"][0]["run"]
    result = subprocess.run(
        ["bash", "-c", script],
        env=dict(
            os.environ,
            REUSED=reused,
            REUSE=reuse,
            FULL=full,
            GITHUB_STEP_SUMMARY=str(tmp_path / "summary"),
        ),
        capture_output=True,
    )
    assert (result.returncode == 0) == accepted


def test_reusable_evidence_workflows_do_not_cancel_one_another():
    for name in ("ci.yml", "integration.yml", "security.yml", "nightly.yml"):
        definition = workflow(name)
        assert "workflow_call" in (definition.get("on") or definition[True])
        assert "concurrency" not in definition


def test_ci_status_includes_every_installed_provider_artifact_shard():
    ci = workflow("ci.yml")["jobs"]
    assert ci["installed-provider-artifacts"]["strategy"]["matrix"]["docker-shard"] == [0, 1, 2, 3]
    assert {"installed-provider-artifacts", "windows-portability"} <= set(ci["ci-status"]["needs"])
    assert (
        ci["ci-status"]["steps"][0]["env"]["INSTALLED_PROVIDER_ARTIFACTS"]
        == "${{ needs.installed-provider-artifacts.result }}"
    )


def test_release_staging_requires_a_fresh_all_findings_assessment():
    jobs = workflow("release-stage.yml")["jobs"]
    assert jobs["reserve"]["needs"] == "security"
    assert jobs["security"]["with"]["mode"] == "release"
    assert jobs["security"]["with"]["head-sha"] == "${{ inputs.candidate_sha }}"


def test_versioned_ruleset_retains_required_merge_identity_without_bypass():
    ruleset = json.loads((REPO_ROOT / "security/release-candidate-ruleset.json").read_text())
    assert ruleset["enforcement"] == "active"
    assert ruleset["conditions"]["ref_name"]["include"] == ["refs/heads/main", "refs/heads/beta"]
    assert ruleset["bypass_actors"] == []
    assert {"pull_request", "required_status_checks"} <= {rule["type"] for rule in ruleset["rules"]}
    assert "pr / required" in str(ruleset)
