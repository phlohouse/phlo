"""Phlo promotion consumes staged bytes; MinIO has an independent publisher."""

import json
import os
import subprocess
import tarfile
import tomllib
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
    assert promotion["jobs"]["promote"]["environment"] == "pypi"
    assert promotion["jobs"]["promote"]["permissions"]["id-token"] == "write"
    assert "id-token" not in promotion["permissions"]
    assert "PYPI_API_TOKEN" not in str(promotion)
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
    assert any(
        step.get("name") == "Scan immutable architecture digest"
        for step in shared["jobs"]["build"]["steps"]
    )


def test_minio_publication_needs_no_phlo_release_or_distributions(tmp_path: Path) -> None:
    publisher = workflow("publish-minio.yml")
    triggers = publisher.get("on") or publisher[True]
    assert set(triggers) == {"workflow_dispatch"}
    assert "refs/heads/main" in publisher["jobs"]["prepare"]["if"]
    assert publisher["jobs"]["images"]["with"]["immutable_tags"] is True
    assert set(publisher["jobs"]) == {"prepare", "images", "sign"}
    assert publisher["jobs"]["sign"]["needs"] == "images"
    assert publisher["jobs"]["sign"]["permissions"]["id-token"] == "write"
    assert publisher["jobs"]["images"]["needs"] == "prepare"
    assert publisher["jobs"]["images"]["uses"] == "./.github/workflows/build-service-images.yml"
    assert set(publisher["jobs"]["images"]["with"]) == {
        "candidate_sha",
        "targets",
        "immutable_tags",
    }
    assert all("environment" not in job for job in publisher["jobs"].values())
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


def test_only_qualified_promotion_owns_python_publication() -> None:
    config = tomllib.loads((WORKFLOWS.parent.parent / "relx.toml").read_text())
    assert config["publish"]["trusted_publishing"] is True
    assert config["publish"]["oidc"] is True
    assert config["publish"]["enabled"] is False
    assert "token_env" not in config["publish"]
    assert not any(channel["publish"] for channel in config["channels"])
    for path in WORKFLOWS.glob("*.yml"):
        content = path.read_text()
        assert "PYPI_API_TOKEN" not in content, path.name
        assert "uv publish" not in content, path.name


@pytest.mark.parametrize("valid", [True, False])
def test_rescan_verifies_exact_subject_and_workflow_and_rejects_unsigned_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid: bool
) -> None:
    rescan = workflow("container-rescan.yml")
    step = next(
        s
        for s in rescan["jobs"]["rescan"]["steps"]
        if s.get("name") == "Verify signatures for every first-party rescan subject"
    )
    published = tmp_path / "published"
    published.mkdir()
    (published / "generated-service-images.json").write_text(
        json.dumps(
            [
                {"image": "ghcr.io/phlohouse/phlo-api:0.15.0", "digest": "sha256:" + "a" * 64},
                {"image": "ghcr.io/phlohouse/phlo-minio:0.29.1", "digest": "sha256:" + "b" * 64},
                {"image": "postgres:18", "digest": "sha256:" + "c" * 64},
            ]
        )
    )
    cosign = tmp_path / "cosign"
    cosign.write_text(
        "#!/bin/bash\nset -eu\n"
        '[[ "$1" = verify && "$2" = --certificate-identity && "$4" = --certificate-oidc-issuer ]]\n'
        '[[ "$5" = https://token.actions.githubusercontent.com ]]\n'
        'case "$6" in\n'
        " ghcr.io/phlohouse/phlo-api@sha256:*) workflow=release-promotion.yml;;\n"
        " ghcr.io/phlohouse/phlo-minio@sha256:*) workflow=publish-minio.yml;;\n"
        " *) exit 9;;\nesac\n"
        '[[ "$3" = "https://github.com/phlohouse/phlo/.github/workflows/$workflow@refs/heads/main" ]]\n'
        'echo "$6" >> verified-subjects\n'
        '[[ "$VALID_SIGNATURE" = true ]] || exit 1\n'
        "echo '[{\"verified\":true}]'\n"
    )
    cosign.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("VALID_SIGNATURE", str(valid).lower())
    result = subprocess.run(
        ["bash", "-c", step["run"]], cwd=tmp_path, capture_output=True, text=True
    )
    assert (result.returncode == 0) is valid, result.stderr
    if valid:
        assert (tmp_path / "verified-subjects").read_text().splitlines() == [
            "ghcr.io/phlohouse/phlo-api@sha256:" + "a" * 64,
            "ghcr.io/phlohouse/phlo-minio@sha256:" + "b" * 64,
        ]
        assert len(list((tmp_path / "reports").glob("*-signatures.json"))) == 2
    scan = next(
        s
        for s in rescan["jobs"]["rescan"]["steps"]
        if s.get("name") == "Rescan immutable images with fresh Trivy data"
    )
    assert scan["if"] == "${{ !cancelled() }}"


@pytest.mark.parametrize("promoted", [True, False])
def test_release_archives_real_qualification_only_after_matched_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, promoted: bool
) -> None:
    step = next(
        s
        for s in workflow("release-promotion.yml")["jobs"]["promote"]["steps"]
        if s.get("name")
        == "Archive qualification and exact-byte publication evidence on the release"
    )
    candidate = tmp_path / "inputs" / ("candidate-" + "e" * 40)
    candidate.mkdir(parents=True)
    (candidate / "bom.json").write_text(
        json.dumps({"artifacts": [{"kind": "source", "version": "0.15.0"}]})
    )
    (candidate / "provenance.json").write_text('{"staged":true}')
    evidence = tmp_path / "inputs/evidence"
    evidence.mkdir()
    (evidence / "bundle.json").write_text('{"qualifying":true}')
    (tmp_path / "authorization.json").write_text('{"authorized":true}')
    (tmp_path / "promotion-receipt.json").write_text(
        json.dumps(
            {"status": "promoted" if promoted else "partial_publication", "success": promoted}
        )
    )
    gh = tmp_path / "gh"
    gh.write_text(
        '#!/bin/bash\n[[ "$1 $2 $3" = "release upload v0.15.0" ]] || exit 1\necho "$4" > uploaded-archive\n'
    )
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("CANDIDATE_SHA", "e" * 40)
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    result = subprocess.run(
        ["bash", "-c", step["run"]], cwd=tmp_path, capture_output=True, text=True
    )
    assert (result.returncode == 0) is promoted, result.stderr
    assert (tmp_path / "uploaded-archive").exists() is promoted
    if promoted:
        with tarfile.open(tmp_path / "release-evidence-123-2.tar.gz") as archive:
            assert (
                archive.extractfile("inputs/evidence/bundle.json").read() == b'{"qualifying":true}'
            )
            assert archive.extractfile("authorization.json").read() == b'{"authorized":true}'
            assert "promotion-receipt.json" in archive.getnames()
            assert "inputs/candidate-" + "e" * 40 + "/bom.json" in archive.getnames()
