"""Reuse requires real full-queue conclusions, not a matching green status name."""

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("ci_evidence", ROOT / "scripts/ci_evidence.py")
assert SPEC and SPEC.loader
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)
SHA = "a" * 40
NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


def example():
    run = {
        "id": 71,
        "run_attempt": 2,
        "head_sha": SHA,
        "repository": {"full_name": "phlohouse/phlo"},
        "head_repository": {"full_name": "phlohouse/phlo"},
        "path": evidence.WORKFLOW,
        "workflow_id": 31,
        "event": "merge_group",
        "head_branch": "gh-readonly-queue/main/pr-123-a",
        "status": "completed",
        "conclusion": "success",
        "run_started_at": (NOW - timedelta(minutes=11)).isoformat(),
    }
    expected = {"pr / required", "ci / core (0)", "ci / core (1)"}
    jobs = [
        {"name": name, "status": "completed", "conclusion": "success", "head_sha": SHA}
        for name in expected
    ]
    manifest = {
        "schema": evidence.SCHEMA,
        "repository": "phlohouse/phlo",
        "sha": SHA,
        "run_id": 71,
        "run_attempt": 2,
        "profile": "full",
        "policy_digest": "digest",
        "expected_jobs": sorted(expected),
        "jobs": {name: "success" for name in expected if name != "pr / required"},
    }
    return run, expected, jobs, manifest


def test_complete_queue_evidence_is_accepted():
    run, expected, jobs, manifest = example()
    evidence.validate_producer(run, {"id": 31, "path": evidence.WORKFLOW}, SHA)
    evidence.validate_manifest(manifest, run, jobs, expected, "digest", now=NOW)


@pytest.mark.parametrize(
    "field,value",
    [
        ("head_sha", "b" * 40),
        ("event", "pull_request"),
        ("workflow_id", 99),
        ("head_branch", "feature/full"),
        ("path", ".github/workflows/other.yml"),
        ("repository", {"full_name": "foreign/repo"}),
        ("head_repository", {"full_name": "fork/phlo"}),
    ],
)
def test_wrong_producer_cannot_supply_evidence(field, value):
    run, _, _, _ = example()
    run[field] = value
    with pytest.raises(ValueError):
        evidence.validate_producer(run, {"id": 31, "path": evidence.WORKFLOW}, SHA)


@pytest.mark.parametrize(
    "mistake",
    [
        "missing-shard",
        "duplicate-shard",
        "skipped",
        "cancelled",
        "failed",
        "wrong-job-sha",
        "selected-profile",
        "stale-attempt",
        "different-policy",
        "forged-conclusion",
        "stale-scan",
    ],
)
def test_incomplete_or_stale_evidence_is_rejected(mistake):
    run, expected, jobs, manifest = example()
    if mistake == "missing-shard":
        jobs.pop()
    elif mistake == "duplicate-shard":
        jobs.append(jobs[0])
    elif mistake in {"skipped", "cancelled", "failed"}:
        jobs[0]["conclusion"] = mistake
    elif mistake == "wrong-job-sha":
        jobs[0]["head_sha"] = "b" * 40
    elif mistake == "selected-profile":
        manifest["profile"] = "selected"
    elif mistake == "stale-attempt":
        manifest["run_attempt"] = 1
    elif mistake == "different-policy":
        manifest["policy_digest"] = "old"
    elif mistake == "forged-conclusion":
        manifest["jobs"]["ci / core (0)"] = "skipped"
    else:
        run["run_started_at"] = (NOW - timedelta(hours=24, seconds=1)).isoformat()
    with pytest.raises(ValueError):
        evidence.validate_manifest(manifest, run, jobs, expected, "digest", now=NOW)


def test_missing_queue_artifact_uses_full_fallback(tmp_path, monkeypatch):
    run, _, _, _ = example()
    monkeypatch.setattr(
        evidence.github,
        "api",
        lambda path: {"id": 31, "path": evidence.WORKFLOW} if "workflows/pr.yml" in path else run,
    )
    monkeypatch.setattr(evidence.github, "pages", lambda *args: [run])

    def missing(*args):
        raise ValueError("expired artifact")

    monkeypatch.setattr(evidence.github, "download_artifact", missing)
    output = tmp_path / "decision.json"
    evidence.reuse(SHA, output)
    assert json.loads(output.read_text()) == {
        "reused": False,
        "sha": SHA,
        "reason": "expired artifact",
    }


def test_full_graph_has_every_matrix_entry_and_new_required_lane():
    jobs = evidence.expected_jobs()
    assert {f"ci / python / core tests (3.12, shard {i})" for i in range(3)} <= jobs
    assert {f"ci / python / installed provider artifacts ({i})" for i in range(4)} <= jobs
    assert {
        "docs / docs / build",
        "ci / behaviour / JavaScript",
        "ci / quality / source and file contracts",
        "containers / safety / container contracts",
    } <= jobs
    assert not any("${{" in name for name in jobs)


def test_emitted_queue_manifest_round_trips_through_the_consumer(tmp_path, monkeypatch):
    run, expected, jobs, _ = example()
    run.update(created_at=run["run_started_at"], run_started_at=datetime.now(UTC).isoformat())
    monkeypatch.setenv("GITHUB_RUN_ID", str(run["id"]))
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", str(run["run_attempt"]))
    monkeypatch.setenv("GITHUB_SHA", SHA)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(evidence, "expected_jobs", lambda: expected)
    monkeypatch.setattr(evidence, "policy_digest", lambda: "digest")
    monkeypatch.setattr(
        evidence.dependency_delta,
        "inventory",
        lambda sha: {"uv.lock": {("PyPI", "example", "1.2")}},
    )
    monkeypatch.setattr(
        evidence.github,
        "api",
        lambda path: {"id": 31, "path": evidence.WORKFLOW} if "workflows/pr.yml" in path else run,
    )
    monkeypatch.setattr(
        evidence.github, "pages", lambda path, key: jobs if key == "jobs" else [run]
    )
    report = {
        "assessment": "complete",
        "head": SHA,
        "mode": "queue",
        "inventory": {"uv.lock": [["PyPI", "example", "1.2"]]},
        "introduced": [],
        "unaccepted": [],
    }
    manifest_path = tmp_path / "manifest.json"

    def download(run, name, destination):
        artifact = destination / name
        artifact.mkdir()
        if name == "dependency-delta":
            (artifact / "dependency-delta.json").write_text(json.dumps(report))
        else:
            (artifact / "merge-validation.json").write_bytes(manifest_path.read_bytes())
        return artifact

    monkeypatch.setattr(evidence.github, "download_artifact", download)
    evidence.emit(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    assert manifest["dependency_assessment"]["inventory"] == {
        "uv.lock": [["PyPI", "example", "1.2"]]
    }
    output = tmp_path / "decision.json"
    evidence.reuse(SHA, output)
    assert json.loads(output.read_text()) == {
        "reused": True,
        "sha": SHA,
        "run_id": 71,
        "run_attempt": 2,
    }
    report["inventory"]["uv.lock"][0][2] = "9.9"
    evidence.reuse(SHA, output)
    assert json.loads(output.read_text())["reused"] is False
    assert "different inventory" in json.loads(output.read_text())["reason"]
