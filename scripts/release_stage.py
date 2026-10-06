#!/usr/bin/env python3
"""Reserve a candidate once, then pin already-built package and image bytes."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import release_candidate_bom as bom
import release_provenance as provenance


def validate_candidate(sha: str) -> None:
    """Require a reviewed commit on the protected default branch."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("candidate must be a full lowercase SHA")
    if os.environ.get("GITHUB_REPOSITORY") != provenance.REPOSITORY:
        raise ValueError("repository is not allowlisted")
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("workflow must execute from main")
    branch = provenance.api(f"repos/{provenance.REPOSITORY}/branches/main")
    if branch.get("protected") is not True:
        raise ValueError("default branch is not protected")
    subprocess.run(["git", "merge-base", "--is-ancestor", sha, "origin/main"], check=True)


def reserve(sha: str) -> None:
    """Claim the SHA before building; even a failed stage may not be rebuilt."""
    validate_candidate(sha)
    if os.environ.get("GITHUB_RUN_ATTEMPT") != "1":
        raise ValueError("staging may not be rerun")
    previous = provenance.pages(f"repos/{provenance.REPOSITORY}/releases", "")
    if any(release.get("tag_name") == f"candidate-{sha}" for release in previous):
        raise ValueError("candidate was already reserved; restaging is forbidden")
    stage_workflow = provenance.api(
        f"repos/{provenance.REPOSITORY}/actions/workflows/release-stage.yml"
    )
    stage_runs = provenance.pages(
        f"repos/{provenance.REPOSITORY}/actions/workflows/{stage_workflow['id']}/runs",
        "workflow_runs",
    )
    if any(
        run.get("display_title") == f"stage {sha}" and run["id"] != int(os.environ["GITHUB_RUN_ID"])
        for run in stage_runs
    ):
        raise ValueError(
            "candidate has a prior staging attempt; deletion/expiry never permits restaging"
        )
    workflow = provenance.api(
        f"repos/{provenance.REPOSITORY}/actions/workflows/release-candidate.yml"
    )
    runs = provenance.pages(
        f"repos/{provenance.REPOSITORY}/actions/workflows/{workflow['id']}/runs?head_sha={sha}",
        "workflow_runs",
    )
    if not runs:
        raise ValueError("candidate has no exact-SHA source-health run")
    latest = max(runs, key=lambda run: run["id"])
    if (
        latest.get("head_sha") != sha
        or latest.get("repository", {}).get("full_name") != provenance.REPOSITORY
        or latest.get("head_repository", {}).get("full_name") != provenance.REPOSITORY
        or latest.get("path", "").split("@")[0] != workflow["path"]
        or latest.get("workflow_id") != workflow["id"]
        or latest.get("status") != "completed"
        or latest.get("conclusion") != "success"
        or latest.get("event") != "push"
        or latest.get("head_branch") != "main"
    ):
        raise ValueError("candidate source-health producer is not trusted/completed/successful")
    # Workflow concurrency serialises claims for one SHA. Keep the draft
    # reservation even after failure, so no later attempt rebuilds it.
    subprocess.run(
        [
            "gh",
            "release",
            "create",
            f"candidate-{sha}",
            "--repo",
            provenance.REPOSITORY,
            "--target",
            sha,
            "--draft",
            "--title",
            f"Candidate {sha}",
            "--notes",
            f"Immutable staging claim, run {os.environ['GITHUB_RUN_ID']}. Do not restage.",
        ],
        check=True,
    )


def pin(sha: str, images: Path, distributions: Path, output: Path) -> None:
    """Copy scanned development digests to staging and record every byte in a BOM."""
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
        staging_ref = f"{target}:candidate-{sha}"
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
        Path.cwd(), sha, output, built_distributions=distributions, image_digests=digests
    )
    provenance_record = {
        "candidate_sha": sha,
        "workflow_sha": os.environ["GITHUB_SHA"],
        "run_id": int(os.environ["GITHUB_RUN_ID"]),
        "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
        "bom_sha256": bom.file_sha256(output / "bom.json"),
        "staged_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "canonical_candidate_digest": staged.canonical_candidate_digest,
    }
    (output / "provenance.json").write_text(
        json.dumps(provenance_record, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    """Run reservation or pinning. The caller owns isolated build jobs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("reserve", "pin"))
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--images", type=Path)
    parser.add_argument("--distributions", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "reserve":
        reserve(args.candidate_sha)
    else:
        pin(args.candidate_sha, args.images, args.distributions, args.output)


if __name__ == "__main__":
    main()
