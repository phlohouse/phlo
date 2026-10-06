#!/usr/bin/env python3
"""Authenticate release producers and native environment approvals using GitHub API data.

Dispatches run trusted main workflows. The inspected SHA is data, bound in the
server-recorded display title and the immutable BOM, not the workflow head SHA.
No API writes occur here.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path

import release_candidate_bom

REPOSITORY = "phlohouse/phlo"
STAGE = ".github/workflows/release-stage.yml"
ACCEPTANCE = ".github/workflows/release-artifact-acceptance.yml"


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
                raise ValueError("GitHub truncated producer history; refusing incomplete evidence")
            return rows
        page += 1


def validate_run(run: dict, workflow: dict, sha: str, title: str) -> None:
    """Reject foreign producers, wrong candidates and incomplete attempts."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("candidate must be a full lowercase commit SHA")
    if (
        run.get("repository", {}).get("full_name") != REPOSITORY
        or workflow.get("path") not in (STAGE, ACCEPTANCE)
        or run.get("head_repository", {}).get("full_name") != REPOSITORY
        or run.get("workflow_id") != workflow.get("id")
        or run.get("path", "").split("@")[0] != workflow.get("path")
        or run.get("head_branch") != "main"
        or run.get("event") != "workflow_dispatch"
        or run.get("display_title") != title
    ):
        raise ValueError("wrong producer or candidate SHA")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise ValueError("candidate has an incomplete or failed functional attempt")


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


def download(run: dict, workflow: dict, sha: str, title: str, names: list[str], dest: Path) -> None:
    """Authenticate before downloading any producer bytes."""
    validate_run(run, workflow, sha, title)
    for name in names:
        download_artifact(run, name, dest)


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


def collect(sha: str, stage_run: int, output: Path, *, evidence: bool) -> None:
    """Fetch the immutable stage and every acceptance attempt for its identity."""
    if api(f"repos/{REPOSITORY}/branches/main").get("protected") is not True:
        raise ValueError("default branch is not protected")
    workflow = api(f"repos/{REPOSITORY}/actions/workflows/release-stage.yml")
    run = api(f"repos/{REPOSITORY}/actions/runs/{stage_run}")
    # Restaging and rerunning the producer would create a second artifact set.
    if run.get("run_attempt") != 1:
        raise ValueError("staging may not be rerun")
    name = f"candidate-{sha}"
    download(run, workflow, sha, f"stage {sha}", [name], output)
    candidate = output / name
    bom = release_candidate_bom.load_bom(candidate / "bom.json")
    record = json.loads((candidate / "provenance.json").read_text(encoding="utf-8"))
    if (
        bom["release_commit"] != sha
        or record.get("candidate_sha") != sha
        or record.get("workflow_sha") != run["head_sha"]
        or record.get("run_id") != run["id"]
        or record.get("run_attempt") != 1
        or record.get("bom_sha256") != release_candidate_bom.file_sha256(candidate / "bom.json")
        or record.get("canonical_candidate_digest") != bom["canonical_candidate_digest"]
    ):
        raise ValueError("staged BOM/provenance is bound to the wrong SHA or attempt")
    release_candidate_bom.verify_staged_distributions(bom, candidate)
    if not evidence:
        return
    workflow = api(f"repos/{REPOSITORY}/actions/workflows/release-artifact-acceptance.yml")
    runs = pages(f"repos/{REPOSITORY}/actions/workflows/{workflow['id']}/runs", "workflow_runs")
    title = f"accept {sha} stage {stage_run}"
    selected = [
        item for item in runs if item.get("display_title", "").startswith(f"accept {sha} stage ")
    ]
    if not selected:
        raise ValueError("no candidate acceptance attempts")
    for summary in selected:
        current = api(f"repos/{REPOSITORY}/actions/runs/{summary['id']}")
        for attempt in range(1, current["run_attempt"] + 1):
            run = api(f"repos/{REPOSITORY}/actions/runs/{current['id']}/attempts/{attempt}")
            names = [f"evidence-{sha}-{run['id']}-{attempt}-{host}" for host in ("amd64", "arm64")]
            download(run, workflow, sha, title, names, output / "evidence")


def validate_approval(environment: dict, reviews: list[dict], actor: str) -> dict:
    """Require configured native reviewers and a non-self approved user."""
    rules = [
        rule
        for rule in environment.get("protection_rules", [])
        if rule.get("type") == "required_reviewers"
    ]
    if len(rules) != 1 or rules[0].get("prevent_self_review") is not True:
        raise ValueError("release environment lacks required reviewers/prevent-self-review")
    reviewers = rules[0].get("reviewers", [])
    if not reviewers:
        raise ValueError("release environment has no required reviewers")
    # User reviewers have directly auditable identity. Teams require a live
    # membership lookup before they can authorise the approving user.
    users = {item["reviewer"]["login"] for item in reviewers if item.get("type") == "User"}
    for review in reviews:
        user = review.get("user", {}).get("login")
        if (
            review.get("state") != "approved"
            or user == actor
            or not user
            or review.get("user", {}).get("type") != "User"
        ):
            continue
        if not any(env.get("name") == "release" for env in review.get("environments", [])):
            continue
        if user in users:
            return review
        for item in reviewers:
            if item.get("type") == "Team":
                team = item["reviewer"]
                membership = api(f"orgs/phlohouse/teams/{team['slug']}/memberships/{user}")
                if membership.get("state") == "active":
                    return review
    raise ValueError("no authenticated required-reviewer approval for this run")


def live_approval() -> dict:
    """Read protection and approval for the current trusted promotion run."""
    if (
        os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
        raise ValueError("promotion must execute in the allowlisted repository on main")
    if api(f"repos/{REPOSITORY}/branches/main").get("protected") is not True:
        raise ValueError("default branch is not protected")
    run_id = int(os.environ["GITHUB_RUN_ID"])
    run = api(f"repos/{REPOSITORY}/actions/runs/{run_id}")
    workflow = api(f"repos/{REPOSITORY}/actions/workflows/release-promotion.yml")
    if (
        run.get("repository", {}).get("full_name") != REPOSITORY
        or run.get("head_repository", {}).get("full_name") != REPOSITORY
        or run.get("head_branch") != "main"
        or run.get("head_sha") != os.environ.get("GITHUB_SHA")
        or run.get("run_attempt") != int(os.environ["GITHUB_RUN_ATTEMPT"])
        or run.get("workflow_id") != workflow["id"]
        or run.get("path", "").split("@")[0] != workflow["path"]
        or run.get("event") != "workflow_dispatch"
    ):
        raise ValueError("promotion must execute from protected default branch")
    environment = api(f"repos/{REPOSITORY}/environments/release")
    reviews = api(f"repos/{REPOSITORY}/actions/runs/{run_id}/approvals")
    review = validate_approval(environment, reviews, run["actor"]["login"])
    if review["user"]["login"] == run.get("triggering_actor", {}).get("login"):
        raise ValueError("approver may not approve their own rerun")
    return review


def verify_live_authorization(record: dict) -> None:
    """A hand-written JSON record can never authorise --execute."""
    review = live_approval()
    reference = f"https://github.com/{REPOSITORY}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    if (
        record.get("release_owner") != review["user"]["login"]
        or record.get("approval_reference") != reference
    ):
        raise ValueError("authorization is not this run's native approval")


def authorize(bom_path: Path, evidence: Path, output: Path) -> None:
    """Create a BOM/evidence-bound record from live native approval, not user input."""
    import promote_release_candidate as promotion

    review = live_approval()
    run_id = int(os.environ["GITHUB_RUN_ID"])
    bom = promotion.load_candidate_bom(bom_path)
    qualification = promotion.qualify_evidence_set(
        promotion._collect_bundles([evidence]),
        bom,
        now_utc=promotion.utc_now(),
        staged_utc=json.loads((bom_path.parent / "provenance.json").read_text(encoding="utf-8"))[
            "staged_utc"
        ],
    )
    record = {
        "schema": promotion.AUTHORIZATION_SCHEMA,
        "authorized": True,
        "candidate": {key: bom[key] for key in ("release_commit", "canonical_candidate_digest")},
        "release_owner": review["user"]["login"],
        "target_channel": "pypi+ghcr+github-releases",
        "approval_reference": f"https://github.com/{REPOSITORY}/actions/runs/{run_id}",
        "authorized_utc": promotion.format_utc(promotion.utc_now()),
        "evidence_bundle_checksums": sorted(qualification.checksums),
    }
    output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    """Run authenticated collection or native approval validation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("collect", "authorize"))
    parser.add_argument("--candidate-sha")
    parser.add_argument("--stage-run", type=int)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--bom", type=Path)
    args = parser.parse_args()
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise ValueError("repository is not allowlisted")
    if args.command == "collect":
        collect(args.candidate_sha, args.stage_run, args.output, evidence=args.evidence is not None)
    else:
        authorize(args.bom, args.evidence, args.output)


if __name__ == "__main__":
    main()
