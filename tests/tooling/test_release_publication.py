"""Phlo releases publish the bytes their smoke tested; MinIO has an independent publisher."""

import json
import os
import shlex
import subprocess
import tomllib
from itertools import pairwise
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


def test_release_publishes_from_main_only_after_its_own_smoke() -> None:
    release = workflow("release-promotion.yml")
    triggers = release.get("on") or release[True]
    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"] == {"branches": ["main"]}
    assert set(triggers["workflow_dispatch"]["inputs"]) == {"sha"}
    assert release["permissions"] == {"contents": "read"}
    jobs = release["jobs"]
    assert set(jobs) == {"select", "security", "build", "publish", "arm64"}
    assert jobs["select"]["if"] == (
        "github.repository == 'phlohouse/phlo' && github.ref == 'refs/heads/main'"
    )
    select = jobs["select"]["steps"][-1]["run"]
    assert "release_publish.py select" in select and "--require" in select
    assert jobs["build"]["uses"] == "./.github/workflows/release-build.yml"
    assert jobs["build"]["with"]["rehearsal"] is False
    assert jobs["build"]["if"] == "needs.select.outputs.release == 'true'"
    publish = jobs["publish"]
    assert publish["needs"] == ["select", "security", "build"]
    assert publish["environment"] == "pypi"
    assert publish["permissions"]["id-token"] == "write"
    assert publish["permissions"]["attestations"] == "write"
    assert publish["concurrency"]["cancel-in-progress"] is False
    uses = [step.get("uses", "") for step in publish["steps"]]
    assert any(use.startswith("actions/attest-build-provenance@") for use in uses)
    assert any(
        step.get("with", {}).get("name") == "release-set-${{ needs.build.outputs.sha }}"
        for step in publish["steps"]
    )
    assert "release_publish.py publish --candidate candidate" in publish["steps"][-1]["run"]
    # Building happens once, in release-build.yml; publishing never rebuilds.
    assert "uv build" not in str(release)
    assert "id-token" not in str(workflow("release-build.yml"))
    assert jobs["arm64"]["if"] == "always() && needs.select.outputs.release == 'true'"
    assert "actions/attest-build-provenance" not in str(workflow("release-rehearsal.yml"))
    for retired in (
        "release-stage.yml",
        "release-merge.yml",
        "release-artifact-acceptance.yml",
    ):
        assert not (WORKFLOWS / retired).exists(), retired


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
    assert callers == {"release-build.yml"}
    assert workflow("release-build.yml")["jobs"]["images"]["with"]["rehearsal"] == (
        "${{ inputs.rehearsal }}"
    )
    assert "-development:" in str(images["jobs"]["prepare"])
    shared = workflow("build-service-images.yml")
    assert "needs.build.result == 'success'" in shared["jobs"]["merge"]["if"]
    assert len(shared["jobs"]["build"]["strategy"]["matrix"]["architecture"]) == 2
    assert any(
        step.get("name") == "Scan immutable architecture digest"
        for step in shared["jobs"]["build"]["steps"]
    )


def test_rehearsal_builds_and_smokes_without_any_publication_authority() -> None:
    rehearsal = workflow("release-rehearsal.yml")
    jobs = rehearsal["jobs"]
    for job in jobs.values():
        permissions = job.get("permissions", {})
        assert "id-token" not in permissions
        assert permissions.get("contents", "read") == "read"
        assert "environment" not in job
    assert rehearsal["permissions"] == {"contents": "read"}
    assert jobs["build"]["uses"] == "./.github/workflows/release-build.yml"
    assert jobs["build"]["with"] == {"rehearsal": True}
    assert jobs["report"]["if"] == "failure() && github.event_name == 'schedule'"
    build = workflow("release-build.yml")["jobs"]
    pin = next(step for step in build["pin"]["steps"] if "release_stage.py" in step.get("run", ""))
    assert "mode=(--rehearsal)" in pin["run"]
    smoke = build["smoke"]
    assert smoke["continue-on-error"] == "${{ matrix.host.label == 'arm64' }}"
    assert any("--candidate-bom" in step.get("run", "") for step in smoke["steps"])


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


def test_build_uses_built_distributions_for_images_and_one_bom() -> None:
    build = workflow("release-build.yml")["jobs"]
    assert build["images"]["with"]["distributions_artifact"].startswith("distributions-")
    assert "--distributions distributions" in str(build["pin"])
    assert build["pin"]["steps"][-1]["with"]["if-no-files-found"] == "error"
    assert build["pin"]["steps"][-1]["with"]["name"] == (
        "release-set-${{ needs.distributions.outputs.sha }}"
    )


def test_only_qualified_promotion_owns_python_publication() -> None:
    config = tomllib.loads((WORKFLOWS.parent.parent / "relx.toml").read_text())
    assert config["publish"]["trusted_publishing"] is True
    assert config["publish"]["oidc"] is True
    assert config["publish"]["enabled"] is False
    assert "token_env" not in config["publish"]
    assert not any(channel["publish"] for channel in config["channels"])
    for path in WORKFLOWS.glob("*.yml"):
        parsed = workflow(path.name)
        for job in parsed["jobs"].values():
            for scope in [parsed, job, *job.get("steps", [])]:
                env = scope.get("env", {})
                assert "UV_PUBLISH_TOKEN" not in env, path.name
                assert "${{ secrets.PYPI_API_TOKEN }}" not in env.values(), path.name
                assert "${{ secrets.PYPI_API_TOKEN }}" not in scope.get("with", {}).values(), (
                    path.name
                )
            for step in job.get("steps", []):
                words = shlex.split(step.get("run", ""), comments=True)
                assert ("uv", "publish") not in pairwise(words), path.name


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
    fleet = next(s for s in rescan["jobs"]["rescan"]["steps"] if s.get("id") == "fleet")
    assert fleet["name"] == "Include the latest independently published MinIO image"
    assert step["if"] == scan["if"] == "${{ !cancelled() && steps.fleet.outcome == 'success' }}"
