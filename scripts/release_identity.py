"""Validate the source, distribution, and PyPI identities of a Phlo release.

Hashes sdist/wheel artifacts, cross-checks project name and version across
pyproject metadata, packaged metadata, and PyPI listings, verifies remote
sdist content hashes, then emits the resulting publish plan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import tarfile
import tempfile
import tomllib
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from email.parser import Parser
from pathlib import Path

from packaging.utils import canonicalize_name
from packaging.version import Version


class ReleaseIdentityError(ValueError):
    """A tag, source tree, artifact, or remote index is not release coherent."""


@dataclass(frozen=True)
class Project:
    name: str
    version: str
    source: str


@dataclass(frozen=True)
class Artifact:
    filename: str
    project: str
    version: str
    kind: str
    path: str
    sha256: str
    content_sha256: str | None = None


def _project(path: Path, root: Path) -> Project:
    with path.open("rb") as handle:
        metadata = tomllib.load(handle).get("project", {})
    name, version = metadata.get("name"), metadata.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        raise ReleaseIdentityError(
            f"{path.relative_to(root)} must declare static project.name and project.version"
        )
    try:
        Version(version)
    except ValueError as exc:
        raise ReleaseIdentityError(
            f"{path.relative_to(root)} has invalid PEP 440 version {version!r}"
        ) from exc
    return Project(canonicalize_name(name), version, str(path.relative_to(root)))


def source_projects(root: Path) -> list[Project]:
    """Collect every source project under the root and reject duplicate names."""
    projects = [_project(root / "pyproject.toml", root)]
    projects.extend(_project(path, root) for path in sorted(root.glob("packages/*/pyproject.toml")))
    names = [project.name for project in projects]
    if len(names) != len(set(names)):
        raise ReleaseIdentityError("source project names must be unique after normalization")
    return projects


def validate_source(root: Path, tag: str) -> dict[str, object]:
    """Check the tag and support manifest against source project versions."""
    projects = source_projects(root)
    root_project = next(project for project in projects if project.source == "pyproject.toml")
    if tag != f"v{root_project.version}":
        raise ReleaseIdentityError(
            f"tag {tag!r} must equal the root version tag {f'v{root_project.version}'!r}"
        )

    support_path = root / "registry/support/v1.json"
    packaged_support_path = root / "src/phlo/support_data/v1.json"
    support = json.loads(support_path.read_text(encoding="utf-8"))
    if support["current_release"]["version"] != root_project.version:
        raise ReleaseIdentityError(
            "support current_release.version must match the root project version"
        )
    if support_path.read_bytes() != packaged_support_path.read_bytes():
        raise ReleaseIdentityError(
            "packaged support manifest must exactly match registry/support/v1.json"
        )

    source_versions = {project.name: project.version for project in projects}
    for entry in support["release_set"]["packages"]:
        name = canonicalize_name(entry["name"])
        if source_versions.get(name) != entry["version"]:
            raise ReleaseIdentityError(
                f"support release_set package {name!r} does not match its source project version"
            )
    return {"tag": tag, "projects": [asdict(project) for project in projects]}


def _metadata_from_wheel(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as archive:
        metadata_names = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1:
            raise ReleaseIdentityError(f"{path} must contain exactly one wheel METADATA file")
        content = archive.read(metadata_names[0]).decode("utf-8")
    return _metadata_fields(path, content)


def _metadata_from_sdist(path: Path) -> tuple[str, str]:
    with tarfile.open(path, "r:*") as archive:
        members = [
            member
            for member in archive.getmembers()
            if member.name.endswith("/PKG-INFO") and member.name.count("/") == 1
        ]
        if len(members) != 1:
            raise ReleaseIdentityError(f"{path} must contain exactly one PKG-INFO file")
        handle = archive.extractfile(members[0])
        assert handle is not None
        content = handle.read().decode("utf-8")
    return _metadata_fields(path, content)


def _metadata_fields(path: Path, content: str) -> tuple[str, str]:
    fields = Parser().parsestr(content, headersonly=True)
    name, version = fields.get("Name"), fields.get("Version")
    if name is None or version is None:
        raise ReleaseIdentityError(f"{path} metadata must declare Name and Version")
    return canonicalize_name(name), version


def _validate_packaged_support(path: Path, kind: str, expected: bytes) -> None:
    if kind == "wheel":
        with zipfile.ZipFile(path) as archive:
            names = [
                name for name in archive.namelist() if name.endswith("phlo/support_data/v1.json")
            ]
            contents = [archive.read(name) for name in names]
    else:
        with tarfile.open(path, "r:*") as archive:
            members = [
                member
                for member in archive.getmembers()
                if member.name.endswith("phlo/support_data/v1.json")
            ]
            contents = []
            for member in members:
                handle = archive.extractfile(member)
                assert handle is not None
                contents.append(handle.read())
    if contents != [expected]:
        raise ReleaseIdentityError(f"{path.name} does not contain the checked support manifest")


def artifacts_for(root: Path, artifact_paths: list[Path]) -> list[Artifact]:
    """Match built wheels and sdists against source projects and return validated artifacts."""
    expected = {project.name: project for project in source_projects(root)}
    support = (root / "registry/support/v1.json").read_bytes()
    artifacts: list[Artifact] = []
    for path in sorted(artifact_paths):
        if path.suffix == ".whl":
            name, version, kind = *_metadata_from_wheel(path), "wheel"
        elif path.name.endswith(".tar.gz"):
            name, version, kind = *_metadata_from_sdist(path), "sdist"
        else:
            continue
        project = expected.get(name)
        if project is None or project.version != version:
            raise ReleaseIdentityError(
                f"{path.name} does not match a source project name and version"
            )
        if name == "phlo":
            _validate_packaged_support(path, kind, support)
        artifacts.append(
            Artifact(
                filename=path.name,
                project=name,
                version=version,
                kind=kind,
                path=str(path),
                sha256=_sha256(path),
                content_sha256=_sdist_content_sha256(path) if kind == "sdist" else None,
            )
        )
    expected_kinds = {
        (project.name, kind) for project in expected.values() for kind in ("wheel", "sdist")
    }
    actual_kinds = {(artifact.project, artifact.kind) for artifact in artifacts}
    if actual_kinds != expected_kinds:
        raise ReleaseIdentityError(
            f"built artifacts are incomplete; missing={sorted(expected_kinds - actual_kinds)!r}, "
            f"unexpected={sorted(actual_kinds - expected_kinds)!r}"
        )
    if len({artifact.filename for artifact in artifacts}) != len(artifacts):
        raise ReleaseIdentityError("built artifact filenames must be unique")
    return artifacts


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


# Digest the sdist's logical content rather than the archive bytes: gzip
# timestamps and member ordering differ between rebuilds of an identical
# source tree, so only this normalized hash is stable across builds.
def _sdist_content_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with tarfile.open(path, "r:*") as archive:
        members = sorted(archive.getmembers(), key=lambda member: member.name)
        names: set[str] = set()
        for member in members:
            name = posixpath.normpath(member.name)
            if name in {".", ".."} or name.startswith(("../", "/")):
                raise ReleaseIdentityError(f"{path} has unsafe sdist member {member.name!r}")
            if name in names:
                raise ReleaseIdentityError(f"{path} has duplicate sdist member {name!r}")
            names.add(name)
            if member.isdir():
                continue
            if not member.isfile():
                raise ReleaseIdentityError(f"{path} has non-regular sdist member {name!r}")
            handle = archive.extractfile(member)
            assert handle is not None
            payload = handle.read()
            encoded = name.encode()
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
    return digest.hexdigest()


def _pypi_files(project: str, version: str) -> dict[str, tuple[str, bool, str]]:
    try:
        with urllib.request.urlopen(
            f"https://pypi.org/pypi/{project}/{version}/json", timeout=30
        ) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        raise
    except urllib.error.URLError as exc:
        raise ReleaseIdentityError(
            f"could not retrieve PyPI metadata for {project} {version}: {exc.reason}"
        ) from exc
    return {
        entry["filename"]: (entry["digests"]["sha256"], bool(entry["yanked"]), entry["url"])
        for entry in payload["urls"]
    }


def _remote_sdist_content_sha256(url: str, expected_hash: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = response.read()
    if hashlib.sha256(payload).hexdigest() != expected_hash:
        raise ReleaseIdentityError(
            f"PyPI sdist download from {url} does not match its advertised SHA-256"
        )
    with tempfile.NamedTemporaryFile(suffix=".tar.gz") as temporary:
        temporary.write(payload)
        temporary.flush()
        return _sdist_content_sha256(Path(temporary.name))


def publish_plan(artifacts: list[Artifact]) -> tuple[list[Artifact], list[str]]:
    """Split artifacts into uploads and PyPI conflicts."""
    remote: dict[tuple[str, str], dict[str, tuple[str, bool, str]]] = {}
    upload: list[Artifact] = []
    conflicts: list[str] = []
    for artifact in artifacts:
        key = (artifact.project, artifact.version)
        if key not in remote:
            remote[key] = _pypi_files(*key)
        files = remote[key]
        published = files.get(artifact.filename)
        if published is None:
            upload.append(artifact)
            continue
        # PyPI refuses any re-upload under an already-used filename, so an
        # existing file is accepted as published when it is byte-identical
        # (wheel) or has identical normalized content (sdist).
        actual_hash, yanked, url = published
        if yanked:
            conflicts.append(f"{artifact.project} {artifact.filename} is yanked on PyPI")
        elif artifact.kind == "sdist" and artifact.content_sha256 == _remote_sdist_content_sha256(
            url, actual_hash
        ):
            continue
        elif actual_hash != artifact.sha256:
            conflicts.append(f"{artifact.project} {artifact.filename} has a different PyPI SHA-256")
    for (project, version), files in remote.items():
        expected_filenames = {
            artifact.filename
            for artifact in artifacts
            if (artifact.project, artifact.version) == (project, version)
        }
        unexpected = sorted(set(files) - expected_filenames)
        if unexpected:
            conflicts.append(f"{project} {version} has unexpected PyPI files: {unexpected!r}")
    return upload, conflicts


def _write_json(path: Path | None, value: object) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if path is None:
        print(rendered, end="")
    else:
        path.write_text(rendered, encoding="utf-8")


def main() -> None:
    """Run the release identity CLI subcommand."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    source = subparsers.add_parser("source")
    source.add_argument("--root", type=Path, default=Path())
    source.add_argument("--tag", required=True)
    source.add_argument("--output", type=Path)
    built = subparsers.add_parser("artifacts")
    built.add_argument("--root", type=Path, default=Path())
    built.add_argument("--tag", required=True)
    built.add_argument("--artifact", type=Path, action="append", required=True)
    built.add_argument("--output", type=Path, required=True)
    plan = subparsers.add_parser("publish-plan")
    plan.add_argument("--manifest", type=Path, required=True)
    plan.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        if args.command == "source":
            _write_json(args.output, validate_source(args.root.resolve(), args.tag))
        elif args.command == "artifacts":
            source_manifest = validate_source(args.root.resolve(), args.tag)
            source_manifest["artifacts"] = [
                asdict(artifact) for artifact in artifacts_for(args.root.resolve(), args.artifact)
            ]
            _write_json(args.output, source_manifest)
        else:
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            artifacts = [Artifact(**artifact) for artifact in manifest["artifacts"]]
            upload, conflicts = publish_plan(artifacts)
            if conflicts:
                raise ReleaseIdentityError("; ".join(conflicts))
            _write_json(args.output, {"upload": [asdict(artifact) for artifact in upload]})
    except ReleaseIdentityError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
