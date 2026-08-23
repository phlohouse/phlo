"""Contracts for the exact-SHA release-candidate gate.

Reads workflow and ruleset files as data and asserts the gate holds: the
candidate aggregates every release-critical lane, reusable workflows cannot
cancel each other, release tags bind to the exact candidate SHA, and manual
publishing cannot bypass identity checks.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"


def test_candidate_status_fails_closed_on_every_release_critical_lane() -> None:
    candidate = yaml.safe_load((WORKFLOW_ROOT / "release-candidate.yml").read_text())

    triggers = candidate.get("on") or candidate[True]
    assert triggers["pull_request"]["branches"] == ["main", "beta"]
    assert triggers["push"]["branches"] == ["main", "beta"]
    assert "merge_group" in triggers
    assert candidate["jobs"]["status"]["name"] == "release candidate / status"
    assert candidate["jobs"]["status"]["if"] == "always()"
    assert candidate["jobs"]["status"]["needs"] == ["ci", "integration", "security", "nightly"]
    assert (
        "release-candidate-evidence-${{ github.sha }}"
        in (WORKFLOW_ROOT / "release-candidate.yml").read_text()
    )


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
    assert '"${INSTALLED_PROVIDER_ARTIFACTS}"' in (WORKFLOW_ROOT / "ci.yml").read_text()


def test_release_tag_requires_successful_aggregate_for_its_exact_sha() -> None:
    release = (WORKFLOW_ROOT / "release.yml").read_text()

    assert "CANDIDATE_SHA: ${{ github.sha }}" in release
    assert "check-runs?per_page=100" in release
    assert (
        'select(.name == "release candidate / status" and .app.slug == "github-actions")' in release
    )
    assert "failure|cancelled|skipped|timed_out|action_required" in release
    assert "checks: read" in release
    assert 'candidate_sha="$(git rev-parse HEAD)"' in release
    assert "Require successful release-candidate evidence for tag target" in release
    assert "Prove the ReleaseX candidate and proposed tag identity" in release
    assert "relx/release/monorepo/${RELEASE_BRANCH}-release-set" in release
    assert "refs/tags/${tag} already exists" in release
    assert "Verify created tag and GitHub Release target the candidate" in release


def test_release_publish_validates_the_complete_artifact_manifest() -> None:
    release = (WORKFLOW_ROOT / "release.yml").read_text()

    assert "Validate the complete built artifact manifest" in release
    assert "scripts/release_identity.py artifacts" in release
    assert "scripts/release_identity.py publish-plan" in release
    assert "All expected artifacts are already published with matching hashes." in release
    assert "Remove already-published artifacts" not in release


def test_manual_artifact_workflow_cannot_bypass_release_identity_checks() -> None:
    workflow = (WORKFLOW_ROOT / "publish.yml").read_text()

    assert "uv publish" not in workflow
    assert "name: Build Package Artifacts" in workflow


def test_versioned_ruleset_requires_review_and_candidate_status() -> None:
    ruleset = json.loads((REPO_ROOT / "security/release-candidate-ruleset.json").read_text())

    assert ruleset["enforcement"] == "active"
    assert ruleset["conditions"]["ref_name"]["include"] == ["refs/heads/main", "refs/heads/beta"]
    assert ruleset["bypass_actors"] == [
        {
            "actor_id": "REPLACE_WITH_RELEASE_EMERGENCY_TEAM_ID",
            "actor_type": "Team",
            "bypass_mode": "always",
        }
    ]
    rule_types = {rule["type"] for rule in ruleset["rules"]}
    assert {"pull_request", "required_status_checks"} <= rule_types
    assert (
        "release candidate / status"
        in (REPO_ROOT / "security/release-candidate-ruleset.json").read_text()
    )
