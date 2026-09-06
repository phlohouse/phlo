"""Unit tests for the release source and artifact identity boundary.

Source validation requires the git tag to match the root version, the support
BOM and its packaged copy to agree, and wheel/sdist metadata plus embedded
support data to match; publish plans reject conflicting or unexpected remote
artifacts.
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from zipfile import ZipFile

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "release_identity", REPO_ROOT / "scripts/release_identity.py"
)
assert SPEC is not None and SPEC.loader is not None
release_identity = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release_identity
SPEC.loader.exec_module(release_identity)
ReleaseIdentityError = release_identity.ReleaseIdentityError
validate_source = release_identity.validate_source
Artifact = release_identity.Artifact


def _write_release(
    root: Path, *, root_version: str = "1.2.3", support_version: str = "1.2.3"
) -> None:
    (root / "registry/support").mkdir(parents=True)
    (root / "src/phlo/support_data").mkdir(parents=True)
    (root / "packages/example").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "phlo"\nversion = "1.2.3"\n')
    (root / "packages/example/pyproject.toml").write_text(
        '[project]\nname = "phlo-example"\nversion = "4.5.6"\n'
    )
    support = {
        "current_release": {"version": support_version},
        "release_set": {"packages": [{"name": "phlo-example", "version": "4.5.6"}]},
    }
    content = json.dumps(support)
    (root / "registry/support/v1.json").write_text(content)
    (root / "src/phlo/support_data/v1.json").write_text(content)


def test_source_manifest_requires_tag_to_match_root_version(tmp_path: Path) -> None:
    _write_release(tmp_path)

    with pytest.raises(ReleaseIdentityError, match="root version tag"):
        validate_source(tmp_path, "v1.2.4")


def test_source_manifest_rejects_support_bom_version_mismatch(tmp_path: Path) -> None:
    _write_release(tmp_path, support_version="1.2.2")

    with pytest.raises(ReleaseIdentityError, match="current_release.version"):
        validate_source(tmp_path, "v1.2.3")


def test_source_manifest_rejects_packaged_support_copy_mismatch(tmp_path: Path) -> None:
    _write_release(tmp_path)
    (tmp_path / "src/phlo/support_data/v1.json").write_text("{}")

    with pytest.raises(ReleaseIdentityError, match="packaged support manifest"):
        validate_source(tmp_path, "v1.2.3")


def _candidate_baseline(root: Path) -> None:
    _write_release(root)
    for args in (
        ("init",),
        ("add", "."),
        ("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "baseline"),
        ("tag", "v1.2.3"),
    ):
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "commit.gpgsign=false",
                "-c",
                "tag.gpgsign=false",
                "-c",
                "core.hooksPath=/dev/null",
                *args,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def _advance_candidate(root: Path, *, provider_version: str = "4.5.7") -> None:
    path = root / "pyproject.toml"
    path.write_text(path.read_text().replace('"1.2.3"', '"1.2.4"'))
    path = root / "packages/example/pyproject.toml"
    path.write_text(path.read_text().replace('"4.5.6"', f'"{provider_version}"'))
    for relative in ("registry/support/v1.json", "src/phlo/support_data/v1.json"):
        path = root / relative
        path.write_text(
            path.read_text()
            .replace('"1.2.3"', '"1.2.4"')
            .replace('"4.5.6"', f'"{provider_version}"')
        )


def test_candidate_rejects_reused_version_after_dependency_metadata_changes(tmp_path: Path) -> None:
    _candidate_baseline(tmp_path)
    _advance_candidate(tmp_path, provider_version="4.5.6")
    manifest = tmp_path / "packages/example/pyproject.toml"
    manifest.write_text(manifest.read_text() + 'dependencies = ["phlo>=1.2.4"]\n')
    with pytest.raises(ReleaseIdentityError, match="phlo-example 4.5.6"):
        release_identity.validate_candidate(tmp_path, "v1.2.3")


def test_candidate_also_rejects_rebuilding_unchanged_published_versions(tmp_path: Path) -> None:
    _candidate_baseline(tmp_path)
    _advance_candidate(tmp_path, provider_version="4.5.6")
    with pytest.raises(ReleaseIdentityError, match="fresh version for every package"):
        release_identity.validate_candidate(tmp_path, "v1.2.3")


def test_candidate_accepts_fresh_versions_for_the_whole_workspace(tmp_path: Path) -> None:
    _candidate_baseline(tmp_path)
    _advance_candidate(tmp_path)
    assert release_identity.validate_candidate(tmp_path, "v1.2.3")["release_candidate"] is True


def test_candidate_skips_ordinary_development_without_a_root_version_bump(tmp_path: Path) -> None:
    _candidate_baseline(tmp_path)
    assert release_identity.validate_candidate(tmp_path, "v1.2.3")["release_candidate"] is False


def test_candidate_fails_closed_when_baseline_is_missing(tmp_path: Path) -> None:
    _candidate_baseline(tmp_path)
    with pytest.raises(ReleaseIdentityError, match="cannot read release baseline"):
        release_identity.validate_candidate(tmp_path, "missing-tag")


def _write_wheel(path: Path, name: str, version: str, support: bytes | None = None) -> None:
    with ZipFile(path, "w") as archive:
        archive.writestr(
            f"{name.replace('-', '_')}-{version}.dist-info/METADATA",
            f"Name: {name}\nVersion: {version}\n",
        )
        if support is not None:
            archive.writestr("phlo/support_data/v1.json", support)


def _write_sdist(path: Path, name: str, version: str, support: bytes | None = None) -> None:
    with tarfile.open(path, "w:gz") as archive:
        metadata = f"Name: {name}\nVersion: {version}\n".encode()
        metadata_info = tarfile.TarInfo(f"{name}-{version}/PKG-INFO")
        metadata_info.size = len(metadata)
        archive.addfile(metadata_info, io.BytesIO(metadata))
        if support is not None:
            support_info = tarfile.TarInfo(f"{name}-{version}/src/phlo/support_data/v1.json")
            support_info.size = len(support)
            archive.addfile(support_info, io.BytesIO(support))


def test_artifacts_for_validates_archive_metadata_and_packaged_support(tmp_path: Path) -> None:
    _write_release(tmp_path)
    support = (tmp_path / "registry/support/v1.json").read_bytes()
    artifacts_dir = tmp_path / "dist"
    artifacts_dir.mkdir()
    artifacts = [
        artifacts_dir / "phlo-1.2.3-py3-none-any.whl",
        artifacts_dir / "phlo-1.2.3.tar.gz",
        artifacts_dir / "phlo_example-4.5.6-py3-none-any.whl",
        artifacts_dir / "phlo_example-4.5.6.tar.gz",
    ]
    _write_wheel(artifacts[0], "phlo", "1.2.3", support)
    _write_sdist(artifacts[1], "phlo", "1.2.3", support)
    _write_wheel(artifacts[2], "phlo-example", "4.5.6")
    _write_sdist(artifacts[3], "phlo-example", "4.5.6")

    manifest = release_identity.artifacts_for(tmp_path, artifacts)

    assert {(artifact.project, artifact.kind) for artifact in manifest} == {
        ("phlo", "wheel"),
        ("phlo", "sdist"),
        ("phlo-example", "wheel"),
        ("phlo-example", "sdist"),
    }


def test_publish_plan_rejects_conflicting_or_unexpected_remote_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = Artifact(
        filename="phlo-1.2.3-py3-none-any.whl",
        project="phlo",
        version="1.2.3",
        kind="wheel",
        path="dist/phlo-1.2.3-py3-none-any.whl",
        sha256="expected",
    )
    monkeypatch.setattr(
        release_identity,
        "_pypi_files",
        lambda _project, _version: {
            artifact.filename: ("different", False, "https://example.test/wheel"),
            "extra-1.2.3.tar.gz": ("hash", False, "https://example.test/extra"),
        },
    )

    upload, conflicts = release_identity.publish_plan([artifact])

    assert upload == []
    assert "different PyPI SHA-256" in conflicts[0]
    assert "unexpected PyPI files" in conflicts[1]
