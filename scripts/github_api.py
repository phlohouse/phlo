#!/usr/bin/env python3
"""Read the GitHub REST API through the authenticated gh CLI, failing closed."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path

REPOSITORY = "phlohouse/phlo"
RELEASE_BRANCH = "relx/release/monorepo/main-release-set"


def api(path: str) -> object:
    """Read one authenticated GitHub API response, failing on HTTP errors."""
    result = subprocess.run(["gh", "api", path], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def pages(path: str, key: str) -> list[dict]:
    """Read all pages, without silently truncating candidate attempts."""
    rows = []
    page = 1
    while True:
        result = api(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
        batch = result[key] if key else result
        rows.extend(batch)
        if len(batch) < 100:
            if key and result.get("total_count", len(rows)) > len(rows):
                raise ValueError(
                    "GitHub truncated producer history; refusing an incomplete listing"
                )
            return rows
        page += 1


def validate_artifact(artifact: dict, run: dict, name: str) -> None:
    """An artifact must belong to this completed attempt, not a previous rerun."""
    if (
        artifact.get("name") != name
        or artifact.get("expired") is not False
        or not artifact.get("digest", "").startswith("sha256:")
        or artifact.get("workflow_run", {}).get("id") != run["id"]
        or artifact.get("workflow_run", {}).get("head_sha") != run["head_sha"]
        or artifact.get("created_at", "") < run["run_started_at"]
        or artifact.get("created_at", "") > run["updated_at"]
    ):
        raise ValueError("missing, expired or stale-attempt artifact")


def download_artifact(run: dict, name: str, dest: Path) -> Path:
    """Download checksum-verified data from an authenticated run attempt."""
    artifacts = pages(f"repos/{REPOSITORY}/actions/runs/{run['id']}/artifacts", "artifacts")
    matches = [item for item in artifacts if item["name"] == name]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one artifact {name}")
    validate_artifact(matches[0], run, name)
    payload = subprocess.run(
        ["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{matches[0]['id']}/zip"],
        check=True,
        capture_output=True,
    ).stdout
    if "sha256:" + hashlib.sha256(payload).hexdigest() != matches[0]["digest"]:
        raise ValueError("artifact archive checksum mismatch")
    destination = dest / name
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for member in archive.infolist():
            path = (destination / member.filename).resolve()
            if (
                not path.is_relative_to(destination.resolve())
                or (member.external_attr >> 16) & 0o170000 == 0o120000
            ):
                raise ValueError("unsafe artifact member")
        archive.extractall(destination)
    return destination


def merged_release_pr(sha: str) -> dict | None:
    """Find the exact protected-main ReleaseX merge, never a similarly named branch."""
    matches = [
        pr
        for pr in pages(f"repos/{REPOSITORY}/commits/{sha}/pulls", "")
        if pr.get("merged_at")
        and pr.get("merge_commit_sha") == sha
        and pr.get("base", {}).get("ref") == "main"
        and pr.get("base", {}).get("repo", {}).get("full_name") == REPOSITORY
        and pr.get("head", {}).get("repo", {}).get("full_name") == REPOSITORY
        and pr.get("head", {}).get("ref") == RELEASE_BRANCH
        and pr.get("title", "").startswith("chore(release): phlo ")
    ]
    if len(matches) > 1:
        raise ValueError("ambiguous release PR association")
    return matches[0] if matches else None
