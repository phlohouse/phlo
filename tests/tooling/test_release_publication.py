"""Phlo promotion consumes staged bytes; MinIO has an independent publisher."""

import json
import os
import subprocess
from pathlib import Path

import pytest
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
    assert set(triggers) == {"workflow_call"}
    callers = {
        path.name
        for path in WORKFLOWS.glob("*.yml")
        if any(
            job.get("uses") == "./.github/workflows/build-core-services.yml"
            for job in workflow(path.name)["jobs"].values()
        )
    }
    assert callers == {"release-stage.yml"}
    assert "-development:" in str(images["jobs"]["prepare"])
    shared = workflow("build-service-images.yml")
    assert "needs.build.result == 'success'" in shared["jobs"]["merge"]["if"]
    assert len(shared["jobs"]["build"]["strategy"]["matrix"]["architecture"]) == 2
    assert "Scan immutable architecture digest" in str(shared)


def test_minio_publication_needs_no_phlo_release_or_distributions(tmp_path: Path) -> None:
    publisher = workflow("publish-minio.yml")
    triggers = publisher.get("on") or publisher[True]
    assert set(triggers) == {"workflow_dispatch"}
    assert "refs/heads/main" in publisher["jobs"]["prepare"]["if"]
    assert publisher["jobs"]["images"]["with"]["immutable_tags"] is True
    assert "release-stage" not in str(publisher)
    assert "distributions_artifact" not in str(publisher)
    assert "environment" not in publisher["jobs"]["prepare"]
    step = next(
        step for step in publisher["jobs"]["prepare"]["steps"] if step.get("id") == "matrix"
    )
    source = tmp_path / "source"
    manifest = source / "registry/support/v1.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "current_release": {"version": "99.1.0"},
                "release_set": {
                    "services": [
                        {"name": "minio", "image_reference": "ghcr.io/phlohouse/phlo-minio:0.29.1"}
                    ]
                },
            }
        )
    )
    dockerfile = source / "packages/phlo-minio/src/phlo_minio/Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_bytes(
        (WORKFLOWS.parent.parent / "packages/phlo-minio/src/phlo_minio/Dockerfile").read_bytes()
    )
    output = tmp_path / "output"
    subprocess.run(
        ["bash", "-c", step["run"]],
        cwd=source,
        env={**os.environ, "RUNNER_TEMP": str(tmp_path), "GITHUB_OUTPUT": str(output)},
        check=True,
        capture_output=True,
        text=True,
    )
    targets = json.loads(output.read_text().removeprefix("targets="))
    assert len(targets) == 1
    assert targets[0]["image"] == "ghcr.io/phlohouse/phlo-minio:0.29.1"
    assert targets[0]["services"] == ["minio", "minio-setup"]
    context = tmp_path / "generated-service-project" / targets[0]["context"]
    assert (context / "Dockerfile").read_bytes() == dockerfile.read_bytes()


@pytest.mark.parametrize("registry_state", ["missing", "existing", "unauthorized", "unreachable"])
def test_independent_image_tags_are_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry_state: str
) -> None:
    shared = workflow("build-service-images.yml")
    script = next(
        step["run"] for step in shared["jobs"]["merge"]["steps"] if step.get("id") == "manifest"
    )
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/bash\n"
        'if [[ "$3" = create ]]; then touch published; exit 0; fi\n'
        'if [[ "$4" = --format ]]; then printf \'"sha256:%064d"\\n\' 3; exit 0; fi\n'
        'case "$REGISTRY_STATE" in\n'
        "  existing) exit 0;;\n"
        "  missing) echo 'ERROR: image: not found' >&2;;\n"
        "  unauthorized) echo 'ERROR: denied: unauthorized' >&2;;\n"
        "  unreachable) echo 'ERROR: connection refused' >&2;;\n"
        "esac\nexit 1\n"
    )
    docker.chmod(0o755)
    digests = tmp_path / "digests"
    digests.mkdir()
    for digest in ("a" * 64, "b" * 64):
        (digests / digest).touch()
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("REGISTRY_STATE", registry_state)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    monkeypatch.setenv("IMAGE", "ghcr.io/phlohouse/phlo-minio:0.29.1")
    monkeypatch.setenv("IMMUTABLE_TAGS", "true")
    result = subprocess.run(["bash", "-c", script], cwd=tmp_path, capture_output=True, text=True)
    assert (result.returncode == 0) is (registry_state == "missing"), result.stderr
    assert (tmp_path / "published").exists() is (registry_state == "missing")


def test_stage_uses_built_distributions_for_images_and_immutable_bom() -> None:
    stage = workflow("release-stage.yml")
    assert stage["jobs"]["distributions"]["needs"] == "reserve"
    assert stage["jobs"]["images"]["with"]["distributions_artifact"].startswith("distributions-")
    assert "--distributions distributions" in str(stage["jobs"]["pin"])
    assert stage["jobs"]["pin"]["steps"][-1]["with"]["if-no-files-found"] == "error"
