#!/usr/bin/env python3
"""Coordinate existing release dispatches after an authenticated ReleaseX merge."""

from __future__ import annotations

import json
import os
import subprocess
import time

import release_provenance as provenance
import release_stage as staging

ROOT = f"repos/{provenance.REPOSITORY}"


def release_merge(sha: str) -> bool:
    """Accept only the exact merge commit of an in-repository release PR."""
    return provenance.merged_release_pr(sha) is not None


def wait_run(run_id: int, workflow: dict, title: str, deadline: float) -> dict:
    """Wait for the authenticated producer, stopping on any failed attempt."""
    print(
        f"Waiting for {title}: https://github.com/{provenance.REPOSITORY}/actions/runs/{run_id}",
        flush=True,
    )
    while time.monotonic() < deadline:
        run = provenance.api(f"{ROOT}/actions/runs/{run_id}")
        if (
            run.get("repository", {}).get("full_name") != provenance.REPOSITORY
            or run.get("head_repository", {}).get("full_name") != provenance.REPOSITORY
            or run.get("workflow_id") != workflow["id"]
            or run.get("path", "").split("@")[0] != workflow["path"]
            or run.get("head_branch") != "main"
            or run.get("event") != "workflow_dispatch"
            or run.get("display_title") != title
            or run.get("run_attempt") != 1
        ):
            raise ValueError("untrusted producer or rerun; manual recovery required")
        if run.get("status") == "completed":
            if run.get("conclusion") != "success":
                raise ValueError(f"release producer {run_id} failed; stopping")
            return run
        time.sleep(30)
    raise TimeoutError(f"deadline exceeded waiting for release producer {run_id}")


def runs_for(workflow: dict, title: str) -> list[dict]:
    """Find every attempt for the immutable candidate, including failures."""
    return [
        run
        for run in provenance.pages(
            f"{ROOT}/actions/workflows/{workflow['id']}/runs", "workflow_runs"
        )
        if run.get("display_title") == title
    ]


def ensure_runs(filename: str, title: str, inputs: dict, count: int) -> list[dict]:
    """Reuse existing runs, or dispatch once and wait without blind retries."""
    workflow = provenance.api(f"{ROOT}/actions/workflows/{filename}")
    deadline = time.monotonic() + 3 * 60 * 60
    runs = runs_for(workflow, title)
    completed = [wait_run(run["id"], workflow, title, deadline) for run in runs]
    while len(completed) < count:
        if time.monotonic() >= deadline:
            raise TimeoutError("deadline exceeded before release dispatch")
        previous = {run["id"] for run in runs}
        subprocess.run(
            [
                "gh",
                "api",
                "--method",
                "POST",
                f"{ROOT}/actions/workflows/{filename}/dispatches",
                "--input",
                "-",
            ],
            input=json.dumps({"ref": "main", "inputs": inputs}),
            text=True,
            check=True,
        )
        # A lost response or delayed run must never cause a second dispatch.
        discovery_deadline = min(deadline, time.monotonic() + 300)
        while time.monotonic() < discovery_deadline:
            runs = runs_for(workflow, title)
            new = [run for run in runs if run["id"] not in previous]
            if new:
                completed.extend(wait_run(run["id"], workflow, title, deadline) for run in new)
                break
            time.sleep(10)
        else:
            raise TimeoutError("dispatch not visible; inspect GitHub before manual recovery")
    return completed


def coordinate(sha: str) -> None:
    """Keep publication in the existing trusted-publisher workflow."""
    staging.validate_candidate(sha)
    if not release_merge(sha):
        print("Not an exact ReleaseX merge; nothing to publish.")
        return
    deadline = time.monotonic() + 2 * 60 * 60
    while True:
        workflow = provenance.api(f"{ROOT}/actions/workflows/release-candidate.yml")
        runs = provenance.pages(
            f"{ROOT}/actions/workflows/{workflow['id']}/runs?head_sha={sha}", "workflow_runs"
        )
        if runs and max(runs, key=lambda run: run["id"]).get("status") == "completed":
            staging.verify_source_health(sha)
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("source health did not complete before deadline")
        time.sleep(30)
    inputs = {"candidate_sha": sha}
    stages = ensure_runs("release-stage.yml", f"stage {sha}", inputs, 1)
    if len(stages) != 1:
        raise ValueError("multiple staging attempts; refusing promotion")
    stage = stages[0]["id"]
    inputs = {**inputs, "stage_run": str(stage)}
    # Each dispatch produces two complete bundles, one per architecture.
    ensure_runs("release-artifact-acceptance.yml", f"accept {sha} stage {stage}", inputs, 2)
    ensure_runs(
        "release-promotion.yml",
        f"promote {sha} stage {stage} execute true",
        {**inputs, "execute": True},
        1,
    )


if __name__ == "__main__":
    coordinate(os.environ["CANDIDATE_SHA"])
