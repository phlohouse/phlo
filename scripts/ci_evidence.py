#!/usr/bin/env python3
"""Record full queue validation and reuse only authenticated exact-SHA evidence."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import container_security
import dependency_delta
import release_provenance as github
import select_ci
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/pr.yml"
SCHEMA = "phlo.merge-validation/v1"
AGGREGATE = "pr / required"


def policy_digest(root: Path = ROOT) -> str:
    """Bind the evidence to the checked-in validation implementation and policy."""
    paths = (
        subprocess.check_output(
            ["git", "ls-files", "-z", ".github", "scripts", "security", "pyproject.toml"], cwd=root
        )
        .decode()
        .strip("\0")
        .split("\0")
    )
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.encode() + b"\0" + (root / path).read_bytes() + b"\0")
    return digest.hexdigest()


def expected_jobs(root: Path = ROOT) -> set[str]:
    """Expand the full workflow graph, including every configured matrix entry."""
    groups = select_ci.select(set(), root)["groups"]
    targets = container_security.affected_images({"pyproject.toml"}, root)["include"]

    def expand(path: str, prefix: str = "") -> set[str]:
        jobs = yaml.safe_load((root / path).read_text())["jobs"]
        names = set()
        for key, job in jobs.items():
            name = prefix + job.get("name", key)
            if "uses" in job:
                names.update(expand(job["uses"].removeprefix("./"), name + " / "))
                continue
            matrix = job.get("strategy", {}).get("matrix", {})
            if not matrix:
                names.add(name)
                continue
            if "include" in matrix:
                if matrix["include"] != "${{ fromJSON(needs.ci-config.outputs.groups) }}":
                    raise ValueError("Unknown dynamic matrix; cannot prove full coverage")
                entries = groups
            else:
                keys = list(matrix)
                values = [targets if key == "target" else matrix[key] for key in keys]
                if any(not isinstance(value, list) for value in values):
                    raise ValueError("Unknown dynamic matrix; cannot prove full coverage")
                entries = [dict(zip(keys, row, strict=True)) for row in itertools.product(*values)]
            for entry in entries:

                def substitute(match: re.Match, values: dict = entry) -> str:
                    value = values
                    for part in match[1].split("."):
                        value = value[part]
                    return str(value)

                names.add(re.sub(r"\$\{\{ matrix\.([\w.-]+) \}\}", substitute, name))
        return names

    return expand(WORKFLOW)


def validate_producer(run: dict, workflow: dict, sha: str) -> None:
    """A selected PR, different workflow, repository or SHA is never reusable."""
    if (
        not re.fullmatch(r"[0-9a-f]{40}", sha)
        or run.get("head_sha") != sha
        or run.get("repository", {}).get("full_name") != github.REPOSITORY
        or run.get("head_repository", {}).get("full_name") != github.REPOSITORY
        or run.get("workflow_id") != workflow.get("id")
        or workflow.get("path") != WORKFLOW
        or run.get("path", "").split("@")[0] != WORKFLOW
        or run.get("event") != "merge_group"
        or not run.get("head_branch", "").startswith(
            ("gh-readonly-queue/main/", "gh-readonly-queue/beta/")
        )
    ):
        raise ValueError("Not allowlisted exact-SHA full merge-queue evidence")


def successful_jobs(jobs: list[dict], expected: set[str], sha: str) -> dict[str, str]:
    """Reject incomplete matrices and skipped, cancelled or failed required jobs."""
    matching = [job for job in jobs if job["name"] in expected]
    if len(matching) != len(expected) or {job["name"] for job in matching} != expected:
        raise ValueError("Missing or duplicate required matrix entry")
    if any(
        job.get("status") != "completed"
        or job.get("conclusion") != "success"
        or job.get("head_sha") != sha
        for job in matching
    ):
        raise ValueError("Required job did not complete successfully at the exact SHA")
    return {job["name"]: "success" for job in matching}


def validate_manifest(
    manifest: dict,
    run: dict,
    jobs: list[dict],
    expected: set[str],
    digest: str,
    *,
    now: datetime | None = None,
) -> None:
    """Cross-check claims against live run-attempt metadata and current policy."""
    if (
        manifest.get("schema") != SCHEMA
        or manifest.get("repository") != github.REPOSITORY
        or manifest.get("sha") != run["head_sha"]
        or manifest.get("run_id") != run["id"]
        or manifest.get("run_attempt") != run["run_attempt"]
        or manifest.get("profile") != "full"
        or manifest.get("policy_digest") != digest
        or set(manifest.get("expected_jobs", [])) != expected
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
    ):
        raise ValueError("Stale, selected, unsuccessful or inconsistent validation manifest")
    started = datetime.fromisoformat(run["run_started_at"])
    age = (now or datetime.now(UTC)) - started
    if not timedelta(0) <= age <= timedelta(hours=24):
        raise ValueError("Queue security assessment is older than 24 hours")
    live = successful_jobs(jobs, expected, run["head_sha"])
    if manifest.get("jobs") != {name: result for name, result in live.items() if name != AGGREGATE}:
        raise ValueError("Manifest conclusions differ from GitHub job evidence")


def run_jobs(run: dict) -> list[dict]:
    return github.pages(
        f"repos/{github.REPOSITORY}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs",
        "jobs",
    )


def assessment_record(run: dict, destination: Path) -> dict:
    """Bind the successful assessment to the entire revision-bound dependency inventory."""
    artifact = github.download_artifact(run, "dependency-delta", destination)
    payload = (artifact / "dependency-delta.json").read_bytes()
    report = json.loads(payload)
    inventory = {
        path: [list(package) for package in sorted(packages)]
        for path, packages in dependency_delta.inventory(run["head_sha"]).items()
    }
    if (
        report.get("assessment") != "complete"
        or report.get("head") != run["head_sha"]
        or report.get("mode") != "queue"
        or report.get("inventory") != inventory
        or report.get("introduced") != []
        or report.get("unaccepted") != []
    ):
        raise ValueError("Queue assessment is incomplete, blocked or covers a different inventory")
    return {
        "artifact": "dependency-delta",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "inventory": inventory,
    }


def emit(output: Path) -> None:
    """Run inside the required aggregate, after all full-queue lanes succeed."""
    workflow = github.api(f"repos/{github.REPOSITORY}/actions/workflows/pr.yml")
    run = github.api(f"repos/{github.REPOSITORY}/actions/runs/{os.environ['GITHUB_RUN_ID']}")
    validate_producer(run, workflow, os.environ["GITHUB_SHA"])
    if run["run_attempt"] != int(os.environ["GITHUB_RUN_ATTEMPT"]):
        raise ValueError("Producer attempt changed")
    expected = expected_jobs()
    jobs = run_jobs(run)
    conclusions = successful_jobs(jobs, expected - {AGGREGATE}, run["head_sha"])
    durations = {
        job["name"]: (
            datetime.fromisoformat(job["completed_at"]) - datetime.fromisoformat(job["started_at"])
        ).total_seconds()
        for job in jobs
        if job.get("completed_at") and job.get("started_at")
    }
    manifest = {
        "schema": SCHEMA,
        "repository": github.REPOSITORY,
        "sha": run["head_sha"],
        "workflow": WORKFLOW,
        "run_id": run["id"],
        "run_attempt": run["run_attempt"],
        "profile": "full",
        "policy_digest": policy_digest(),
        "expected_jobs": sorted(expected),
        "jobs": conclusions,
        "job_seconds": durations,
        "created_at": run["created_at"],
        "started_at": run["run_started_at"],
    }
    with tempfile.TemporaryDirectory() as temporary:
        manifest["dependency_assessment"] = assessment_record(run, Path(temporary))
    output.write_text(json.dumps(manifest, indent=2) + "\n")


def reuse(sha: str, output: Path) -> None:
    """Missing or invalid evidence means full validation, never a green shortcut."""
    decision = {"reused": False, "sha": sha}
    try:
        workflow = github.api(f"repos/{github.REPOSITORY}/actions/workflows/pr.yml")
        runs = github.pages(
            f"repos/{github.REPOSITORY}/actions/workflows/{workflow['id']}/runs?head_sha={sha}",
            "workflow_runs",
        )
        queue = [run for run in runs if run.get("event") == "merge_group"]
        if not queue:
            raise ValueError("No merge-queue evidence; run the full fallback")
        run = github.api(
            f"repos/{github.REPOSITORY}/actions/runs/{max(queue, key=lambda r: r['id'])['id']}"
        )
        validate_producer(run, workflow, sha)
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            raise ValueError("Latest queue evidence is not complete and successful")
        with tempfile.TemporaryDirectory() as temporary:
            name = f"merge-validation-{sha}-{run['id']}-{run['run_attempt']}"
            artifact = github.download_artifact(run, name, Path(temporary))
            manifest = json.loads((artifact / "merge-validation.json").read_text())
            validate_manifest(manifest, run, run_jobs(run), expected_jobs(), policy_digest())
            if manifest.get("dependency_assessment") != assessment_record(run, Path(temporary)):
                raise ValueError("Dependency evidence differs from the queue manifest")
        decision.update(reused=True, run_id=run["id"], run_attempt=run["run_attempt"])
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as error:
        decision["reason"] = str(error)
    output.write_text(json.dumps(decision, indent=2) + "\n")
    print(json.dumps(decision))
    if "GITHUB_OUTPUT" in os.environ:
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
            stream.write(f"reused={str(decision['reused']).lower()}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("emit", "reuse"))
    parser.add_argument("--sha", default=os.environ.get("GITHUB_SHA"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "emit":
        emit(args.output)
    else:
        reuse(args.sha, args.output)


if __name__ == "__main__":
    main()
