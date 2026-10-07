"""The only publication lane consumes staged bytes behind native approval."""

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github/workflows"


def workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def test_release_pr_creation_is_preserved_without_tagging_or_rebuild_publication() -> None:
    release = workflow("release.yml")
    assert set(release["jobs"]) == {"release-pr"}
    assert "release pr" in str(release)
    assert "release tag" not in str(release)
    assert not (WORKFLOWS / "publish.yml").exists()
    assert not (WORKFLOWS / "release-recovery.yml").exists()


def test_promotion_accepts_no_operator_evidence_or_authorization_refs() -> None:
    promotion = workflow("release-promotion.yml")
    triggers = promotion.get("on") or promotion[True]
    assert set(triggers) == {"workflow_dispatch"}
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"candidate_sha", "stage_run", "execute"}
    assert inputs["execute"]["default"] is False
    assert promotion["jobs"]["promote"]["environment"] == "release"
    assert promotion["jobs"]["promote"]["if"] == "inputs.execute"
    scripts = [step["run"] for step in promotion["jobs"]["promote"]["steps"] if "run" in step]
    assert "collect" in scripts[0] and "authorize" in scripts[0]
    assert "--execute" in scripts[1]
    assert "uv build" not in str(promotion)
    assert "ref: ${{ inputs.candidate_sha }}" not in str(promotion)


def test_development_images_cannot_rebuild_release_images() -> None:
    images = workflow("build-core-services.yml")
    triggers = images.get("on") or images[True]
    assert "release" not in triggers
    assert "-development:" in str(images["jobs"]["prepare"])
    assert "needs.build.result == 'success'" in images["jobs"]["merge"]["if"]
    assert len(images["jobs"]["build"]["strategy"]["matrix"]["architecture"]) == 2
    assert "Scan immutable architecture digest" in str(images)


def test_stage_uses_built_distributions_for_images_and_immutable_bom() -> None:
    stage = workflow("release-stage.yml")
    assert stage["jobs"]["distributions"]["needs"] == "reserve"
    assert stage["jobs"]["images"]["with"]["distributions_artifact"].startswith("distributions-")
    assert "--distributions distributions" in str(stage["jobs"]["pin"])
    assert stage["jobs"]["pin"]["steps"][-1]["with"]["if-no-files-found"] == "error"
