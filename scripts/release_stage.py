#!/usr/bin/env python3
"""Pin already-built package and image bytes into the release BOM."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import github_api as github
import release_candidate_bom as bom


def validate_candidate(sha: str) -> None:
    """Require a reviewed commit on the protected default branch."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("candidate must be a full lowercase SHA")
    if os.environ.get("GITHUB_REPOSITORY") != github.REPOSITORY:
        raise ValueError("repository is not allowlisted")
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("workflow must execute from main")
    branch = github.api(f"repos/{github.REPOSITORY}/branches/main")
    if branch.get("protected") is not True:
        raise ValueError("default branch is not protected")
    subprocess.run(["git", "merge-base", "--is-ancestor", sha, "origin/main"], check=True)


def validate_rehearsal(sha: str) -> None:
    """Allow a rehearsal BOM for any commit of this repository, but never a release tag."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("candidate must be a full lowercase SHA")
    if os.environ.get("GITHUB_REPOSITORY") != github.REPOSITORY:
        raise ValueError("repository is not allowlisted")
    subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], check=True)


def pin(
    sha: str, images: Path, distributions: Path, output: Path, *, rehearsal: bool = False
) -> None:
    """Copy scanned development digests to staging and record every byte in a BOM.

    A rehearsal copies the same digests to ``rehearsal-<sha>`` tags instead of
    ``candidate-<sha>`` and records ``"rehearsal": true``, which publishing
    refuses, so a rehearsal BOM can never be published.
    """
    if rehearsal:
        validate_rehearsal(sha)
    else:
        validate_candidate(sha)
    if output.exists():
        raise ValueError("staging destination already exists")
    records = json.loads(images.read_text(encoding="utf-8"))
    digests = {}
    for record in records:
        image = record["image"]
        digest = record["digest"]
        repository, tag, _ = bom.parse_image_reference(image)
        if (
            not repository.startswith(bom.FIRST_PARTY_IMAGE_PREFIX)
            or not repository.endswith("-development")
            or tag != sha
            or not bom.IMAGE_DIGEST_RE.fullmatch(digest)
        ):
            raise ValueError("image manifest does not name this candidate's development digest")
        target = repository.removesuffix("-development")
        tag_prefix = "rehearsal" if rehearsal else "candidate"
        staging_ref = f"{target}:{tag_prefix}-{sha}"
        subprocess.run(
            [
                "docker",
                "buildx",
                "imagetools",
                "create",
                "--tag",
                staging_ref,
                f"{repository}@{digest}",
            ],
            check=True,
        )
        if bom.resolve_image_digest(staging_ref) != digest:
            raise ValueError("image copy changed the pinned manifest bytes")
        if target in digests:
            raise ValueError("duplicate candidate image")
        digests[target] = digest
    staged = bom.stage(
        Path.cwd(),
        sha,
        output,
        built_distributions=distributions,
        image_digests=digests,
        # A release ships PyPI's bytes for unchanged versions. A rehearsal tests the
        # current tree, so it always rebuilds.
        reuse_published=not rehearsal,
    )
    provenance_record = {
        "candidate_sha": sha,
        "workflow_sha": os.environ["GITHUB_SHA"],
        "run_id": int(os.environ["GITHUB_RUN_ID"]),
        "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
        "bom_sha256": bom.file_sha256(output / "bom.json"),
        "staged_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "canonical_candidate_digest": staged.canonical_candidate_digest,
        "rehearsal": rehearsal,
    }
    (output / "provenance.json").write_text(
        json.dumps(provenance_record, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    """Pin the release set. The caller owns isolated build jobs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("pin",))
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--images", type=Path)
    parser.add_argument("--distributions", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--rehearsal",
        action="store_true",
        help="Pin a rehearsal BOM under rehearsal-<sha> tags; never publishable.",
    )
    args = parser.parse_args()
    pin(
        args.candidate_sha,
        args.images,
        args.distributions,
        args.output,
        rehearsal=args.rehearsal,
    )


if __name__ == "__main__":
    main()
