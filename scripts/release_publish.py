#!/usr/bin/env python3
"""Publish the exact bytes a release build tested, resuming safely after a partial run.

The release workflow builds the release set once, runs the artifact golden
path against those bytes, and then calls ``publish`` on the pinned BOM. Each
step checks what is already public first:

* the ``v<version>`` tag is created only when missing, and must name the
  release commit;
* a PyPI file that already exists is skipped when its SHA-256 matches and
  stops the release when it differs;
* an image tag that already exists must resolve to the tested digest;
* a GitHub release asset that already exists must carry the same digest.

So rerunning a failed release completes it forward and never republishes
different bytes. Any mismatch fails closed for a human to resolve.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import github_api as github
import release_candidate_bom as bom_module

SIGNING_IDENTITY = (
    "https://github.com/phlohouse/phlo/.github/workflows/release-promotion.yml@refs/heads/main"
)
SIGNING_ISSUER = "https://token.actions.githubusercontent.com"
PYPI_SIMPLE = "https://pypi.org/simple/"
TOKEN_VARIABLES = (
    "UV_PUBLISH_TOKEN",
    "UV_PUBLISH_USERNAME",
    "UV_PUBLISH_PASSWORD",
    "PYPI_API_TOKEN",
)
# PyPI's JSON API can lag an upload by a few minutes.
PYPI_VISIBILITY_ATTEMPTS = 10
PYPI_VISIBILITY_DELAY_SECONDS = 30

Runner = Callable[[list[str]], str]
PypiFiles = Callable[[str, str], dict[str, tuple[str, str]]]


class PublishError(RuntimeError):
    """Public state differs from the tested bytes, or a publish step failed."""


def run(command: list[str]) -> str:
    """Run one command, echoing it, and return its stdout."""
    print("+ " + " ".join(command), flush=True)
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode:
        raise PublishError(
            f"{command[0]} failed ({result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


class DryRun:
    """Run only read-only commands; print the publishing commands instead."""

    dry_run = True

    def __call__(self, command: list[str]) -> str:
        read_only = command[:2] == ["git", "ls-remote"] or (
            command[:2] == ["gh", "api"] and "--method" not in command
        )
        if read_only:
            return run(command)
        print("would run: " + " ".join(command), flush=True)
        return ""


def _dry(runner: Runner) -> bool:
    return getattr(runner, "dry_run", False)


def pypi_files(project: str, version: str) -> dict[str, tuple[str, str]]:
    """Return {filename: (sha256, url)} for one PyPI release, empty when unpublished."""
    return bom_module._pypi_release_files(project, version, allow_missing=True)


def is_release_merge(sha: str) -> bool:
    """True only for the exact merge commit of the ReleaseX release PR into main."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise PublishError("release commit must be a full lowercase SHA")
    return github.merged_release_pr(sha) is not None


class Release:
    """One pinned release set: the BOM and the staged distribution files."""

    def __init__(self, candidate: Path) -> None:
        provenance = json.loads((candidate / "provenance.json").read_text(encoding="utf-8"))
        if provenance.get("rehearsal", False) is not False:
            raise PublishError("refusing to publish a rehearsal BOM")
        self.bom = bom_module.load_bom(candidate / "bom.json")
        self.bom_path = candidate / "bom.json"
        self.commit = str(self.bom["release_commit"])
        if provenance.get("candidate_sha") != self.commit:
            raise PublishError("provenance and BOM name different commits")
        self.version = next(
            str(artifact["version"])
            for artifact in self.bom["artifacts"]
            if artifact["kind"] == bom_module.KIND_SOURCE
        )
        self.tag = f"v{self.version}"
        staged = bom_module.verify_staged_distributions(self.bom, candidate)
        self.files = {bom_module.file_sha256(path): path for path in staged}
        self.distributions = [
            artifact
            for artifact in self.bom["artifacts"]
            if artifact["kind"] in (bom_module.KIND_SDIST, bom_module.KIND_WHEEL)
        ]
        self.images = [
            artifact
            for artifact in self.bom["artifacts"]
            if artifact["kind"] == bom_module.KIND_FIRST_PARTY_IMAGE
        ]
        if not self.distributions or not self.images:
            raise PublishError("BOM has no distributions or no first-party images")

    def path(self, artifact: dict[str, object]) -> Path:
        return self.files[str(artifact["digest"])]


def publish_tag(release: Release, runner: Runner) -> None:
    """Create the release tag on the release commit, or accept an identical one."""
    output = runner(
        ["git", "ls-remote", "origin", f"refs/tags/{release.tag}", f"refs/tags/{release.tag}^{{}}"]
    )
    refs = dict(line.split()[::-1] for line in output.splitlines() if line.strip())
    existing = refs.get(f"refs/tags/{release.tag}^{{}}", refs.get(f"refs/tags/{release.tag}"))
    if existing == release.commit:
        print(f"{release.tag} already names {release.commit}")
        return
    if existing:
        raise PublishError(f"{release.tag} names {existing}, not release commit {release.commit}")
    runner(["git", "tag", release.tag, release.commit])
    runner(["git", "push", "origin", f"refs/tags/{release.tag}"])


def missing_pypi_files(release: Release, files: PypiFiles) -> list[Path]:
    """Return staged files PyPI lacks; stop on any public file with different bytes."""
    published: dict[tuple[str, str], dict[str, tuple[str, str]]] = {}
    missing = []
    for artifact in release.distributions:
        key = (str(artifact["name"]), str(artifact["version"]))
        if key not in published:
            published[key] = files(*key)
        path = release.path(artifact)
        public = published[key].get(path.name)
        if public is None:
            missing.append(path)
        elif public[0] != artifact["digest"]:
            raise PublishError(f"PyPI {path.name} differs from the tested bytes")
    return missing


def verify_pypi(
    release: Release,
    files: PypiFiles,
    *,
    attempts: int = PYPI_VISIBILITY_ATTEMPTS,
    delay: float = PYPI_VISIBILITY_DELAY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Wait for every staged file to be public on PyPI with the tested digest."""
    for attempt in range(1, attempts + 1):
        missing = missing_pypi_files(release, files)
        if not missing:
            return
        if attempt < attempts:
            print(f"PyPI does not list {len(missing)} files yet; retrying in {delay}s", flush=True)
            sleep(delay)
    raise PublishError(f"PyPI still does not list: {', '.join(path.name for path in missing)}")


def publish_pypi(release: Release, runner: Runner, files: PypiFiles) -> None:
    """Upload only the files PyPI lacks, through trusted publishing, then verify."""
    if any(os.environ.get(name) for name in TOKEN_VARIABLES):
        raise PublishError("PyPI publication uses trusted publishing, not token credentials")
    missing = missing_pypi_files(release, files)
    if missing:
        runner(
            [
                "uv",
                "publish",
                "--trusted-publishing",
                "always",
                "--check-url",
                PYPI_SIMPLE,
                *map(str, missing),
            ]
        )
    else:
        print("every distribution is already on PyPI with the tested digest")
    if not _dry(runner):
        verify_pypi(release, files)


def _remote_digest(reference: str) -> str | None:
    try:
        return bom_module.resolve_image_digest(reference)
    except bom_module.BomError as exc:
        if any(text in str(exc).lower() for text in ("manifest unknown", "not found")):
            return None
        raise


def publish_images(
    release: Release,
    runner: Runner,
    resolve: Callable[[str], str | None] = _remote_digest,
) -> None:
    """Tag each tested image digest with the version, then sign and verify it."""
    for image in release.images:
        name, digest = str(image["name"]), str(image["digest"])
        target = f"{name}:{release.version}"
        existing = resolve(target)
        if existing is None:
            runner(
                [
                    "docker",
                    "buildx",
                    "imagetools",
                    "create",
                    "--prefer-index=false",
                    "-t",
                    target,
                    f"{name}@{digest}",
                ]
            )
            if _dry(runner):
                continue
            existing = resolve(target)
        if existing != digest:
            raise PublishError(f"{target} resolves to {existing}, not the tested {digest}")
        verify = [
            "cosign",
            "verify",
            "--certificate-identity",
            SIGNING_IDENTITY,
            "--certificate-oidc-issuer",
            SIGNING_ISSUER,
            f"{name}@{digest}",
        ]
        try:
            runner(verify)
            print(f"{name}@{digest} is already signed by this workflow")
        except PublishError:
            runner(["cosign", "sign", "--yes", f"{name}@{digest}"])
            runner(verify)


def _github_release(release: Release, runner: Runner) -> dict[str, object] | None:
    pages = json.loads(
        runner(
            [
                "gh",
                "api",
                "--paginate",
                "--slurp",
                f"repos/{github.REPOSITORY}/releases?per_page=100",
            ]
        )
    )
    return next((item for page in pages for item in page if item["tag_name"] == release.tag), None)


def publish_github_release(release: Release, runner: Runner, notes: Path) -> None:
    """Create the GitHub release with the BOM and distributions, then publish it."""
    assets = {release.bom_path.name: (release.bom_path, bom_module.file_sha256(release.bom_path))}
    for digest, path in release.files.items():
        assets[path.name] = (path, digest)
    current = _github_release(release, runner)
    if current is None:
        runner(
            [
                "gh",
                "release",
                "create",
                release.tag,
                "--repo",
                github.REPOSITORY,
                "--verify-tag",
                "--draft",
                "--title",
                release.tag,
                "--notes-file",
                str(notes),
            ]
        )
        if _dry(runner):
            return
        current = _github_release(release, runner)
        if current is None:
            raise PublishError(f"created release {release.tag} is not visible")
    existing = {asset["name"]: asset.get("digest") for asset in current["assets"]}
    for name, (path, digest) in sorted(assets.items()):
        if name not in existing:
            runner(["gh", "release", "upload", release.tag, str(path), "--repo", github.REPOSITORY])
        elif existing[name] != f"sha256:{digest}":
            raise PublishError(f"release asset {name} differs from the tested bytes")
    if _dry(runner):
        return
    current = _github_release(release, runner)
    uploaded = {asset["name"]: asset.get("digest") for asset in (current or {}).get("assets", [])}
    for name, (_, digest) in assets.items():
        if uploaded.get(name) != f"sha256:{digest}":
            raise PublishError(f"release asset {name} is missing or has the wrong digest")
    if current and current.get("draft"):
        runner(["gh", "release", "edit", release.tag, "--repo", github.REPOSITORY, "--draft=false"])


def release_notes(release: Release, changelog: Path, output: Path) -> Path:
    """Write the changelog section for this version, failing when there is none."""
    text = changelog.read_text(encoding="utf-8")
    heading = re.compile(rf"^## \[phlo {re.escape(release.version)}(?=[\s\]]).*$", re.MULTILINE)
    match = heading.search(text)
    if match is None:
        raise PublishError(f"{changelog} has no section for {release.version}")
    following = re.search(r"^## ", text[match.end() :], re.MULTILINE)
    end = match.end() + following.start() if following else len(text)
    output.write_text(text[match.end() : end].strip() + "\n", encoding="utf-8")
    return output


def publish(candidate: Path, changelog: Path, *, dry_run: bool = False) -> None:
    """Run every publish step in order; each one is safe to repeat."""
    release = Release(candidate)
    notes = release_notes(release, changelog, candidate.parent / "release-notes.md")
    runner: Runner = DryRun() if dry_run else run
    print(
        f"{'Planning' if dry_run else 'Publishing'} {release.tag} from {release.commit}", flush=True
    )
    publish_tag(release, runner)
    publish_pypi(release, runner, pypi_files)
    publish_images(release, runner)
    publish_github_release(release, runner, notes)
    if dry_run:
        return
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(
                f"Published {release.tag}: {len(release.distributions)} distributions, "
                f"{len(release.images)} signed images.\n"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("select", help="Print release=true when SHA is a release merge.")
    select.add_argument("--sha", required=True)
    select.add_argument(
        "--require",
        action="store_true",
        help="Fail instead of printing release=false (manual dispatch).",
    )
    publish_command = commands.add_parser("publish", help="Publish a pinned release BOM.")
    publish_command.add_argument("--candidate", type=Path, required=True)
    publish_command.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    publish_command.add_argument(
        "--dry-run",
        action="store_true",
        help="Read public state and print what a (re)run would publish; change nothing.",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "select":
            selected = is_release_merge(args.sha)
            if args.require and not selected:
                raise PublishError(f"{args.sha} is not the merge commit of a ReleaseX release PR")
            print(f"release={'true' if selected else 'false'}")
        else:
            publish(args.candidate, args.changelog, dry_run=args.dry_run)
    except (PublishError, bom_module.BomError) as exc:
        print(f"release publish: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
