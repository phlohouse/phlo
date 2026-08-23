"""Workflow contracts for the focused container-security lanes.

Pins container-security.yml and container-rescan.yml structure: nightly
schedules, SHA-pinned tooling, affected-image scoping, digest-based nightly
rescans, and no docker build steps in either lane.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _publisher_should_run(discovery_result: str, count: str) -> bool:
    return discovery_result == "success" and count.isdecimal() and int(count) > 0


def test_candidate_comment_publisher_requires_successful_nonempty_discovery() -> None:
    assert not _publisher_should_run("skipped", "")
    assert not _publisher_should_run("success", "")
    assert not _publisher_should_run("success", "0")
    assert _publisher_should_run("success", "1")


def test_container_workflows_run_validation_nightly_with_pinned_tools_and_digest_rescans() -> None:
    validation = (REPO_ROOT / ".github/workflows/container-security.yml").read_text()
    nightly = (REPO_ROOT / ".github/workflows/container-rescan.yml").read_text()
    assert "schedule:" in validation
    assert "pull_request:" in validation
    assert "branches: [main]" in validation
    assert "hadolint/hadolint@sha256:" in validation
    assert "github.event.pull_request.base.sha" in validation
    assert "github.event.pull_request.head.sha" in validation
    assert 'affected-images --base "$BASE_SHA" --head "$HEAD_SHA"' in validation
    assert "affected-images --all" in validation
    assert "container-waivers.md" in validation
    assert "docker build" not in validation
    assert "aquasec/trivy" not in validation
    assert "published-fleet" in nightly
    assert "assemble-rescan-manifest" in nightly
    assert "docker buildx imagetools inspect" in nightly
    assert "generated-service-images.json" in nightly
    assert '"$image@$digest"' in nightly
    assert "gh run download" not in nightly
    assert "docker build --" not in nightly


def test_container_security_pr_paths_cover_image_contract_inputs() -> None:
    validation = (REPO_ROOT / ".github/workflows/container-security.yml").read_text()

    for path in (
        '"packages/**"',
        '"scripts/container_security.py"',
        '"scripts/generated_image_matrix.py"',
        '"security/**"',
        '"pyproject.toml"',
        '"uv.lock"',
    ):
        assert path in validation
    assert "if: always()" in validation


def test_workflow_security_audit_is_nightly_not_a_pr_ci_gate() -> None:
    ci = (REPO_ROOT / ".github/workflows/ci.yml").read_text()
    security = (REPO_ROOT / ".github/workflows/security.yml").read_text()

    assert "make zizmor" not in ci
    assert "security / workflow hardening" in security
    assert "make zizmor" in security


def test_upstream_visibility_is_scheduled_manual_non_blocking_and_strictly_reported() -> None:
    workflow = (REPO_ROOT / ".github/workflows/upstream-image-visibility.yml").read_text()

    for contract in (
        "workflow_dispatch:",
        "schedule:",
        "permissions:",
        "contents: read",
        "timeout-minutes: 90",
        "write-upstream-inventory",
        "summarize-upstream-reports",
        "aquasec/trivy@sha256:",
        "$GITHUB_STEP_SUMMARY",
        "actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f",
        "if: always()",
        "if-no-files-found: error",
        '"$reference" < /dev/null',
    ):
        assert contract in workflow
    for forbidden in ("apply-policy", "container-waivers", "continue-on-error", "--exit-code 1"):
        assert forbidden not in workflow


def test_renovate_image_prs_compare_exact_changed_base_and_candidate_refs() -> None:
    workflow = (REPO_ROOT / ".github/workflows/upstream-image-visibility.yml").read_text()

    for contract in (
        "pull_request:",
        "types: [opened, reopened, synchronize, labeled]",
        "discover-candidates",
        "compare-candidates",
        "github.event.pull_request.user.type == 'Bot'",
        "startsWith(github.head_ref, 'renovate/')",
        "contains(github.event.pull_request.labels.*.name, 'dependencies')",
        "github.event.pull_request.base.sha",
        "github.event.pull_request.head.sha",
        "write-upstream-candidates",
        "--download-db-only",
        "--skip-db-update",
        "compare-upstream-candidates",
        "base-reports/*.json",
        "candidate-reports/*.json",
    ):
        assert contract in workflow
    for publisher_contract in (
        "publish-candidate-comparison",
        "needs: [discover-candidates, compare-candidates]",
        "needs.discover-candidates.result == 'success'",
        "fromJSON(needs.discover-candidates.outputs.count) > 0",
        "actions: read",
        "pull-requests: write",
        "actions/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131",
        "name: upstream-image-candidate-comparison",
        "<!-- phlo-upstream-image-candidate-comparison -->",
        "gh api --paginate",
        "--method PATCH",
        "--method POST",
        "--method DELETE",
        "--input comment-payload.json",
    ):
        assert publisher_contract in workflow
    publisher = workflow.split("  publish-candidate-comparison:", 1)[1].split("\n  scan:", 1)[0]
    assert "permissions:\n      actions: read\n      pull-requests: write" in publisher
    assert "actions/checkout" not in publisher


def test_renovate_config_validation_uses_pinned_node_and_renovate() -> None:
    workflow = (REPO_ROOT / ".github/workflows/renovate-config.yml").read_text()

    assert "pull_request:" in workflow
    assert '"renovate.json"' in workflow
    assert "actions/setup-node@6044e13b5dc448c55e2357c09f80417699197238" in workflow
    assert "npm install --global renovate@44.20.1" in workflow
    assert "renovate-config-validator renovate.json" in workflow
    assert "--no-global" not in workflow
    assert "contents: read" in workflow
