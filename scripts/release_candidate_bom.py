#!/usr/bin/env python3
"""Stage and validate one immutable Phlo release-candidate BOM.

A release candidate is an exact, enumerable artifact set: the release commit
source identity, one sdist and one wheel per published release-set package,
every first-party ``ghcr.io/phlohouse`` service image by digest, every pinned
third-party provider image by digest, and the committed support manifest. The
canonical candidate digest is the SHA-256 of the canonicalised (keys sorted,
whitespace-free) JSON array of artifact digests in BOM order.

The ``stage`` subcommand materializes one candidate staging directory
(``bom.json`` plus downloaded distributions) and refuses to overwrite an
existing BOM: staging is append-only. The ``verify`` subcommand re-derives
every invariant from a BOM document alone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BOM_SCHEMA = "phlo.release-candidate-bom/v1"
ARTIFACT_FIELDS = frozenset({"kind", "name", "version", "digest", "source"})
KIND_SOURCE = "source"
KIND_SUPPORT_MANIFEST = "support-manifest"
KIND_SDIST = "sdist"
KIND_WHEEL = "wheel"
KIND_FIRST_PARTY_IMAGE = "first-party-image"
KIND_PROVIDER_IMAGE = "provider-image"
REQUIRED_KINDS = frozenset(
    {
        KIND_SOURCE,
        KIND_SUPPORT_MANIFEST,
        KIND_SDIST,
        KIND_WHEEL,
        KIND_FIRST_PARTY_IMAGE,
        KIND_PROVIDER_IMAGE,
    }
)
IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
FILE_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
FIRST_PARTY_IMAGE_PREFIX = "ghcr.io/phlohouse/"
SUPPORT_MANIFEST_PATH = "registry/support/v1.json"
PYPI_TIMEOUT_SECONDS = 60


class BomError(ValueError):
    """A BOM document or staged candidate is not release coherent."""


def file_sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file's bytes."""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def canonical_candidate_digest(artifacts: list[dict[str, object]]) -> str:
    """Hash the canonicalised JSON array of artifact digests in BOM order."""
    digests = [str(artifact["digest"]) for artifact in artifacts]
    canonical = json.dumps(digests, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def parse_image_reference(reference: str) -> tuple[str, str, str | None]:
    """Split an image reference into (repository, tag, digest-or-None).

    ``postgres:18@sha256:...`` becomes ``("postgres", "18", "sha256:...")``;
    a reference with no tag and no digest yields an empty tag. Registry
    prefixes are kept in the repository part.
    """
    reference = reference.strip()
    if not reference or reference.startswith("${"):
        raise BomError(f"image reference {reference!r} is not statically resolvable")
    digest: str | None = None
    name = reference
    if "@" in reference:
        name, _, digest_part = reference.partition("@")
        if not IMAGE_DIGEST_RE.match(digest_part):
            raise BomError(f"image reference {reference!r} has a malformed digest")
        digest = digest_part
    tag = ""
    if ":" in name.rsplit("/", 1)[-1]:
        name, _, tag = name.rpartition(":")
    return name, tag, digest


def _run_git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise BomError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout.strip()


class ReleaseTree:
    """Read committed files from a repository, either at a ref or the worktree."""

    def __init__(self, repo_root: Path, release_ref: str | None) -> None:
        self.repo_root = repo_root
        self.release_ref = release_ref

    def read(self, relative_path: str) -> bytes:
        """Return the committed bytes of one file at the pinned identity."""
        if self.release_ref is None:
            return (self.repo_root / relative_path).read_bytes()
        result = subprocess.run(
            ["git", "show", f"{self.release_ref}:{relative_path}"],
            cwd=self.repo_root,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            detail = result.stderr.decode(errors="replace").strip()
            raise BomError(f"{relative_path} is not committed at {self.release_ref}: {detail}")
        return result.stdout

    def yaml_paths(self, root: str) -> list[str]:
        """Return every committed YAML path under one directory at the identity."""
        if self.release_ref is None:
            return sorted(
                str(path.relative_to(self.repo_root)).replace("\\", "/")
                for path in (self.repo_root / root).rglob("*.yaml")
            )
        listing = _run_git(
            self.repo_root, "ls-tree", "-r", "--name-only", self.release_ref, "--", root
        )
        return sorted(line for line in listing.splitlines() if line.endswith(".yaml"))

    def image_references(self, root: str = "packages") -> dict[str, list[str]]:
        """Collect every ``image:`` reference from committed service YAML files."""
        references: dict[str, list[str]] = {}
        image_line = re.compile(r"^\s*image:\s*([^\s#]+)\s*$")
        for relative_path in self.yaml_paths(root):
            for line in self.read(relative_path).decode("utf-8", errors="replace").splitlines():
                match = image_line.match(line)
                if match:
                    references.setdefault(relative_path, []).append(match.group(1))
        return references


def resolve_image_digest(reference: str) -> str:
    """Resolve a registry image reference to its immutable manifest digest."""
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", reference],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise BomError(
            f"could not resolve digest for {reference!r}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    match = re.search(r"^Digest:\s*(sha256:[0-9a-f]{64})$", result.stdout, re.MULTILINE)
    if match is None:
        raise BomError(f"digest resolution for {reference!r} returned no manifest digest")
    return match.group(1)


def _pypi_release_files(project: str, version: str) -> dict[str, tuple[str, str]]:
    """Return {filename: (sha256 hex, download url)} for one PyPI release."""
    url = f"https://pypi.org/pypi/{project}/{version}/json"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=PYPI_TIMEOUT_SECONDS) as response:  # noqa: S310
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise BomError(f"PyPI has no {project} {version} release: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise BomError(f"could not query PyPI for {project} {version}: {exc.reason}") from exc
    files: dict[str, tuple[str, str]] = {}
    for entry in payload.get("urls", []):
        digest = entry.get("digests", {}).get("sha256")
        if entry.get("filename") and digest and entry.get("url"):
            files[str(entry["filename"])] = (str(digest), str(entry["url"]))
    return files


def _download_distribution(
    project: str,
    version: str,
    kind: str,
    distributions_dir: Path,
) -> dict[str, object]:
    """Download one PyPI distribution into the staging dir and return its BOM artifact entry."""
    files = _pypi_release_files(project, version)
    suffix = ".whl" if kind == KIND_WHEEL else ".tar.gz"
    candidates = {name: value for name, value in files.items() if name.endswith(suffix)}
    if len(candidates) != 1:
        raise BomError(
            f"PyPI {project} {version} must publish exactly one {kind}, "
            f"found {sorted(candidates)!r}"
        )
    filename, (expected_digest, url) = next(iter(candidates.items()))
    destination = distributions_dir / filename
    if destination.exists():
        # Staging is append-only: an already-staged file is reused only when its
        # bytes match PyPI's advertised digest exactly.
        staged_digest = file_sha256(destination)
        if staged_digest != expected_digest:
            raise BomError(f"pre-staged {filename} does not match its advertised PyPI SHA-256")
        return {
            "kind": kind,
            "name": project,
            "version": version,
            "digest": staged_digest,
            "source": f"staged:{filename}",
        }
    try:
        with urllib.request.urlopen(url, timeout=PYPI_TIMEOUT_SECONDS) as response:  # noqa: S310
            payload = response.read()
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise BomError(f"could not download {filename}: {exc}") from exc
    actual_digest = hashlib.sha256(payload).hexdigest()
    if actual_digest != expected_digest:
        raise BomError(f"downloaded {filename} does not match its advertised PyPI SHA-256")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return {
        "kind": kind,
        "name": project,
        "version": version,
        "digest": actual_digest,
        "source": f"pypi:{project}/{version}",
    }


_DISTRIBUTION_SUFFIXES = {KIND_SDIST: ".tar.gz", KIND_WHEEL: ".whl"}


def _distribution_filename_matches(filename: str, project: str, kind: str) -> bool:
    """Return True when one built filename is the given project's sdist/wheel."""
    normalized = project.replace("-", "_").lower()
    base = filename.lower()
    if not base.startswith(f"{normalized}-"):
        return False
    return base.endswith(_DISTRIBUTION_SUFFIXES[kind])


def _distribution_version_from_filename(filename: str, project: str) -> str:
    """Extract the version segment from a built distribution filename."""
    normalized = project.replace("-", "_").lower()
    if filename.lower().endswith(".whl"):
        # Wheel filename: {distribution}-{version}(-{build})?-{python}-{abi}-{platform}.whl
        return filename[len(normalized) + 1 :].split("-")[0]
    without_name = filename[len(normalized) + 1 :]
    for suffix in (".tar.gz",):
        if without_name.endswith(suffix):
            return without_name[: -len(suffix)]
    raise BomError(f"cannot parse a version from built distribution {filename!r}")


def _build_distributions_from_tree(
    tree: ReleaseTree,
    projects: list[str],
    version: str,
    distributions_dir: Path,
) -> list[dict[str, object]]:
    """Build each release-set package once from the pinned tree and stage the bytes.

    Python distributions are built once from the release commit. When the
    release_set version is not published on PyPI (a
    pre-release candidate), the stager builds the exact set from the pinned
    tree, records the resulting content digests in the BOM, and stages the
    bytes append-only. The built bytes are never rebuilt or replaced.
    """
    commit = (
        _run_git(tree.repo_root, "rev-parse", "HEAD")
        if tree.release_ref is None
        else _run_git(tree.repo_root, "rev-parse", f"{tree.release_ref}^{{commit}}")
    )
    with tempfile.TemporaryDirectory(prefix="phlo-candidate-build-") as export_dir_name:
        export_dir = Path(export_dir_name)
        archive = subprocess.run(
            ["git", "archive", tree.release_ref or "HEAD"],
            cwd=tree.repo_root,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["tar", "-x", "-C", str(export_dir)],
            input=archive.stdout,
            capture_output=True,
            check=True,
        )
        built_dir = export_dir / "dist"
        built_dir.mkdir(exist_ok=True)
        subprocess.run(
            [
                "uv",
                "build",
                "--all-packages",
                "--out-dir",
                str(built_dir),
            ],
            cwd=export_dir,
            capture_output=True,
            check=True,
        )
        built = sorted(path for path in built_dir.iterdir() if path.is_file())

        staged: list[dict[str, object]] = []
        for project in sorted(projects):
            for kind in (KIND_SDIST, KIND_WHEEL):
                matches = [
                    path
                    for path in built
                    if _distribution_filename_matches(path.name, project, kind)
                ]
                if len(matches) != 1:
                    raise BomError(
                        f"the pinned tree built {len(matches)} {kind} files for {project} "
                        f"{version}, expected exactly one: {[path.name for path in matches]!r}"
                    )
                path = matches[0]
                built_version = _distribution_version_from_filename(path.name, project)
                if built_version != version:
                    raise BomError(
                        f"the pinned tree built {project} {built_version!r} but the support "
                        f"manifest declares release version {version!r}"
                    )
                destination = distributions_dir / path.name
                if destination.exists():
                    if file_sha256(destination) != file_sha256(path):
                        raise BomError(
                            f"pre-staged {path.name} differs from the tree build (staging "
                            "is append-only)"
                        )
                else:
                    shutil.copyfile(path, destination)
                staged.append(
                    {
                        "kind": kind,
                        "name": project,
                        "version": version,
                        "digest": file_sha256(destination),
                        "source": f"local-build:{commit}/{path.name}",
                    }
                )
        return staged


def build_bom_artifacts(
    tree: ReleaseTree,
    *,
    distributions_dir: Path | None = None,
    build_from_tree: bool = False,
) -> list[dict[str, object]]:
    """Enumerate the exact artifact inventory at the pinned identity."""
    support_bytes = tree.read(SUPPORT_MANIFEST_PATH)
    try:
        support = json.loads(support_bytes)
    except json.JSONDecodeError as exc:
        raise BomError(f"{SUPPORT_MANIFEST_PATH} is not valid JSON: {exc}") from exc
    release = support.get("current_release", {})
    version = release.get("version")
    release_set = support.get("release_set", {}).get("packages", [])
    if not isinstance(version, str) or not version:
        raise BomError(f"{SUPPORT_MANIFEST_PATH} does not declare current_release.version")
    package_names = [
        entry.get("name") for entry in release_set if isinstance(entry.get("name"), str)
    ]
    if not package_names:
        raise BomError(f"{SUPPORT_MANIFEST_PATH} release_set declares no packages")

    artifacts: list[dict[str, object]] = []
    if tree.release_ref is None:
        commit = _run_git(tree.repo_root, "rev-parse", "HEAD")
    else:
        commit = _run_git(tree.repo_root, "rev-parse", f"{tree.release_ref}^{{commit}}")
    if not COMMIT_RE.match(commit):
        raise BomError(f"release commit {commit!r} is not a full git SHA")
    remote = _run_git(tree.repo_root, "remote", "get-url", "origin")
    remote = remote.removeprefix("https://github.com/").removesuffix(".git")
    remote = re.sub(r"^git@github\.com:", "", remote)
    artifacts.append(
        {
            "kind": KIND_SOURCE,
            "name": remote or "phlohouse/phlo",
            "version": version,
            "digest": commit,
            "source": "git",
        }
    )
    artifacts.append(
        {
            "kind": KIND_SUPPORT_MANIFEST,
            "name": SUPPORT_MANIFEST_PATH,
            "version": version,
            "digest": hashlib.sha256(support_bytes).hexdigest(),
            "source": f"git:{SUPPORT_MANIFEST_PATH}",
        }
    )

    if build_from_tree:
        if distributions_dir is None:
            raise BomError(
                "staging a candidate BOM requires a distributions directory; "
                "call stage() rather than enumerating artifacts alone"
            )
        artifacts.extend(
            _build_distributions_from_tree(tree, package_names, version, distributions_dir)
        )
    else:
        for project in sorted(package_names):
            for kind in (KIND_SDIST, KIND_WHEEL):
                if distributions_dir is None:
                    raise BomError(
                        "staging a candidate BOM requires a distributions directory; "
                        "call stage() rather than enumerating artifacts alone"
                    )
                artifacts.append(_download_distribution(project, version, kind, distributions_dir))

    seen_images: dict[str, dict[str, object]] = {}
    for relative_path, references in tree.image_references().items():
        for reference in references:
            if reference.startswith("${"):
                default = reference.split(":-", 1)[-1].rstrip("}")
                if default == reference or not default:
                    raise BomError(
                        f"{relative_path} image reference {reference!r} has no static default"
                    )
                reference = default
            name, tag, digest = parse_image_reference(reference)
            if name in seen_images:
                existing = seen_images[name]
                if existing["digest"] != digest and digest is not None:
                    raise BomError(
                        f"image {name!r} is referenced with two digests: "
                        f"{existing['digest']!r} and {digest!r}"
                    )
                continue
            if name.startswith(FIRST_PARTY_IMAGE_PREFIX):
                if digest is not None:
                    raise BomError(
                        f"first-party image {reference!r} must be tag-pinned in service YAML; "
                        "its BOM digest is resolved from the registry"
                    )
                if not tag:
                    raise BomError(f"first-party image {reference!r} has no tag")
                entry = {
                    "kind": KIND_FIRST_PARTY_IMAGE,
                    "name": name,
                    "version": tag,
                    "digest": resolve_image_digest(reference),
                    "source": relative_path,
                }
            else:
                if digest is None:
                    raise BomError(
                        f"provider image {reference!r} in {relative_path} is not digest-pinned; "
                        "a candidate BOM may never reference a mutable tag"
                    )
                entry = {
                    "kind": KIND_PROVIDER_IMAGE,
                    "name": name,
                    "version": tag,
                    "digest": digest,
                    "source": relative_path,
                }
            seen_images[name] = entry
            artifacts.append(entry)

    if not any(artifact["kind"] == KIND_FIRST_PARTY_IMAGE for artifact in artifacts):
        raise BomError("no first-party release images were found at the pinned identity")
    return artifacts


def make_bom(
    artifacts: list[dict[str, object]], release_commit: str, release_ref: str
) -> dict[str, object]:
    """Assemble a validated BOM document with its canonical digest."""
    bom = {
        "schema": BOM_SCHEMA,
        "release_commit": release_commit,
        "release_ref": release_ref,
        "artifacts": artifacts,
    }
    bom["canonical_candidate_digest"] = canonical_candidate_digest(artifacts)
    validate_bom(bom)
    return bom


def validate_bom(bom: object) -> dict[str, object]:
    """Enforce every structural and identity invariant on a BOM document."""
    if not isinstance(bom, dict):
        raise BomError("BOM must be a JSON object")
    if bom.get("schema") != BOM_SCHEMA:
        raise BomError(f"BOM schema must be {BOM_SCHEMA!r}, got {bom.get('schema')!r}")
    if not COMMIT_RE.match(str(bom.get("release_commit", ""))):
        raise BomError("BOM release_commit must be a full 40-hex git SHA")
    artifacts = bom.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise BomError("BOM artifacts must be a non-empty array")
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict) or set(artifact) != ARTIFACT_FIELDS:
            raise BomError(
                f"BOM artifact {index} must have exactly {sorted(ARTIFACT_FIELDS)}, "
                f"got {sorted(artifact) if isinstance(artifact, dict) else artifact!r}"
            )
        for field in ARTIFACT_FIELDS:
            if not isinstance(artifact[field], str) or not artifact[field]:
                raise BomError(f"BOM artifact {index} field {field!r} must be a non-empty string")
        kind = artifact["kind"]
        digest = str(artifact["digest"])
        if kind in (KIND_FIRST_PARTY_IMAGE, KIND_PROVIDER_IMAGE):
            if not IMAGE_DIGEST_RE.match(digest):
                raise BomError(f"BOM image artifact {index} digest must be sha256:<64-hex>")
        elif kind in (KIND_SDIST, KIND_WHEEL, KIND_SUPPORT_MANIFEST):
            if not FILE_DIGEST_RE.match(digest):
                raise BomError(f"BOM artifact {index} digest must be a 64-hex SHA-256")
        elif kind == KIND_SOURCE:
            if not COMMIT_RE.match(digest):
                raise BomError("BOM source artifact digest must be a full 40-hex git SHA")
        else:
            raise BomError(f"BOM artifact {index} has unknown kind {kind!r}")

    kinds = {str(artifact["kind"]) for artifact in artifacts}

    distributions: dict[tuple[str, str], set[str]] = {}
    for artifact in artifacts:
        kind = str(artifact["kind"])
        if kind in (KIND_SDIST, KIND_WHEEL):
            key = (str(artifact["name"]), str(artifact["version"]))
            distributions.setdefault(key, set()).add(kind)
    for (name, version), found in distributions.items():
        if found != {KIND_SDIST, KIND_WHEEL}:
            raise BomError(
                f"BOM must list exactly one sdist and one wheel for {name} {version}; "
                f"found {sorted(found)!r}"
            )

    missing = REQUIRED_KINDS - kinds
    if missing:
        raise BomError(f"BOM is missing required artifact kinds: {sorted(missing)!r}")

    identities = [
        (
            str(artifact["kind"]),
            str(artifact["name"]),
            str(artifact["version"]),
            str(artifact["digest"]),
        )
        for artifact in artifacts
        if artifact["kind"] in (KIND_FIRST_PARTY_IMAGE, KIND_PROVIDER_IMAGE)
    ]
    if len(identities) != len(set(identities)):
        raise BomError("BOM contains duplicate image artifacts")

    recomputed = canonical_candidate_digest(artifacts)
    if bom.get("canonical_candidate_digest") != recomputed:
        raise BomError(
            "BOM canonical_candidate_digest does not match its artifact digests "
            f"(expected {recomputed}, got {bom.get('canonical_candidate_digest')!r})"
        )
    return bom


def verify_staged_distributions(bom: dict[str, object], staging_dir: Path) -> list[Path]:
    """Verify every staged distribution file's digest against the BOM."""
    distributions_dir = staging_dir / "distributions"
    if not distributions_dir.is_dir():
        raise BomError(
            f"candidate staging dir has no distributions/ directory: {distributions_dir}"
        )
    staged = {
        file_sha256(path): path for path in sorted(distributions_dir.iterdir()) if path.is_file()
    }
    verified: list[Path] = []
    seen: set[str] = set()
    for artifact in bom["artifacts"]:
        if artifact["kind"] not in (KIND_SDIST, KIND_WHEEL):
            continue
        digest = str(artifact["digest"])
        path = staged.get(digest)
        if path is None:
            raise BomError(
                f"staged distribution for {artifact['name']} {artifact['version']} "
                f"({artifact['kind']}) is missing or digest-mismatched"
            )
        if digest in seen:
            raise BomError(f"two BOM distributions share one staged file: {path.name}")
        seen.add(digest)
        verified.append(path)
    if len(seen) != sum(
        1 for artifact in bom["artifacts"] if artifact["kind"] in (KIND_SDIST, KIND_WHEEL)
    ):
        raise BomError("staged distributions do not cover the BOM exactly once")
    unexpected = sorted(set(staged) - seen)
    if unexpected:
        raise BomError(f"staging dir holds {len(unexpected)} file(s) that no BOM artifact covers")
    return verified


def load_bom(path: Path) -> dict[str, object]:
    """Load and fully validate a BOM document from disk."""
    try:
        bom = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BomError(f"could not read BOM {path}: {exc}") from exc
    return validate_bom(bom)


@dataclass(frozen=True)
class StagedCandidate:
    """One staged candidate: its BOM document and staging directory."""

    bom: dict[str, object]
    staging_dir: Path

    @property
    def canonical_candidate_digest(self) -> str:
        """Return the candidate's canonical digest."""
        return str(self.bom["canonical_candidate_digest"])

    @property
    def release_commit(self) -> str:
        """Return the candidate's release commit SHA."""
        return str(self.bom["release_commit"])


def stage(
    repo_root: Path,
    release_ref: str | None,
    output_dir: Path,
    *,
    build_from_tree: bool = False,
) -> StagedCandidate:
    """Stage one immutable candidate BOM into an append-only staging directory."""
    tree = ReleaseTree(repo_root, release_ref)
    bom_path = output_dir / "bom.json"
    if bom_path.exists():
        raise BomError(f"refusing to overwrite a staged BOM: {bom_path} (staging is append-only)")
    distributions_dir = output_dir / "distributions"
    distributions_dir.mkdir(parents=True, exist_ok=True)
    artifacts = build_bom_artifacts(
        tree, distributions_dir=distributions_dir, build_from_tree=build_from_tree
    )

    commit = _run_git(
        repo_root,
        "rev-parse",
        f"{release_ref}^{{commit}}" if release_ref else "HEAD",
    )
    bom = make_bom(artifacts, commit, release_ref or commit)
    verify_staged_distributions(bom, output_dir)
    rendered = json.dumps(bom, indent=2, sort_keys=True) + "\n"
    bom_path.write_text(rendered, encoding="utf-8")
    return StagedCandidate(bom=bom, staging_dir=output_dir)


def main(argv: list[str] | None = None) -> int:
    """Run the release candidate BOM CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    stage_parser = subparsers.add_parser("stage", help="Stage one immutable candidate BOM")
    stage_parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    stage_parser.add_argument("--release-ref", default=None, help="Release tag or commit to pin")
    stage_parser.add_argument("--output-dir", type=Path, required=True)
    stage_parser.add_argument(
        "--build-from-tree",
        action="store_true",
        help=(
            "Build the release_set distributions once from the pinned tree instead of "
            "downloading them from PyPI (for pre-release candidates whose version is "
            "not yet published)."
        ),
    )

    verify_parser = subparsers.add_parser("verify", help="Verify a BOM document's invariants")
    verify_parser.add_argument("--bom", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "stage":
            staged = stage(
                args.repo_root.resolve(),
                args.release_ref,
                args.output_dir.resolve(),
                build_from_tree=args.build_from_tree,
            )
            print(
                f"staged candidate {staged.canonical_candidate_digest} "
                f"at {staged.staging_dir} "
                f"({len(staged.bom['artifacts'])} artifacts)"
            )
            return 0
        bom = load_bom(args.bom)
        print(
            f"BOM {args.bom} is valid: candidate "
            f"{bom['canonical_candidate_digest']} ({len(bom['artifacts'])} artifacts)"
        )
        return 0
    except BomError as exc:
        print(f"release candidate BOM error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
