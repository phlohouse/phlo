"""WAP reads must correlate environment, run, ref, and lifecycle evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from phlo_api.api import v1_branch_workflows, v1_jobs, v1_wap
from phlo_api.errors import BackendUnavailableError
from test_v1_branch_workflows import branch_api as branch_api


def _run(logical_id: str, location: str = "prod_loc", **overrides):
    tags = {
        "phlo/run_id": logical_id,
        "phlo/wap_branch": f"pipeline-run-{logical_id}",
        "phlo/ref": f"pipeline-run-{logical_id}",
        "phlo/project_id": "project",
        "phlo/catalog_system": "nessie",
    }
    return {
        "runId": f"dagster-{logical_id}",
        "pipelineName": "daily",
        "status": "SUCCESS",
        "creationTime": 1791023400,
        "startTime": None,
        "endTime": None,
        "assetSelection": None,
        "repositoryOrigin": {"repositoryLocationName": location},
        "tags": [{"key": key, "value": value} for key, value in tags.items()],
        **overrides,
    }


@pytest.fixture
def wap_api(branch_api, monkeypatch, tmp_path):
    client, calls = branch_api
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setattr(v1_wap, "require_scope", v1_branch_workflows.require_scope)
    runs = [_run("prod-one"), _run("stage-one", "stage_loc")]
    references = {
        "references": [
            {"name": f"pipeline-run-{name}", "type": "BRANCH", "hash": revision}
            for name, revision in (("prod-one", "p1"), ("stage-one", "s1"), ("orphan", "o1"))
        ]
    }

    async def graphql(query, variables=None):
        calls.setdefault("graphql", []).append((query, variables))
        return {"data": {"runsOrError": {"__typename": "Runs", "results": runs}}}

    async def nessie(method, path, **kwargs):
        calls["requests"].append((method, path))
        assert method == "GET"
        return references

    monkeypatch.setattr(v1_jobs, "_graphql", graphql)
    monkeypatch.setattr(v1_wap, "_nessie", nessie)
    return client, runs, references, tmp_path, calls


def _write_report(root: Path, row: dict, strategy="branch", **overrides):
    tags = {tag["key"]: tag["value"] for tag in row["tags"]}
    logical_id = tags["phlo/run_id"]
    report_root = root / ".phlo" / "wap-reports"
    launches = report_root / "launches"
    launches.mkdir(parents=True, exist_ok=True)
    binding = {
        "schema_version": "phlo.wap_launch_manifest.v1",
        "logical_run_id": logical_id,
        "dagster_run_id": row["runId"],
        "branch": tags["phlo/wap_branch"],
        "tags": tags,
    }
    raw = json.dumps(binding).encode()
    checksum = hashlib.sha256(raw).hexdigest()
    run_key = hashlib.sha256(logical_id.encode()).hexdigest()[:24]
    launch_path = launches / f"{run_key}.{checksum}.json"
    launch_path.write_bytes(raw)
    report = {
        "schema_version": "phlo.wap_report.v2",
        "run_id": logical_id,
        "dagster_run_id": row["runId"],
        "branch": tags["phlo/wap_branch"],
        "launch_tags": tags,
        "launch_manifest_checksum": checksum,
        "strategy": strategy,
        "status": "checks_failed",
        "updated_at": "2026-10-03T09:11:12Z",
        **overrides,
    }
    (report_root / f"{logical_id}.json").write_text(json.dumps(report))
    return launch_path


def test_environment_inventory_excludes_foreign_and_uncorrelated_refs(wap_api):
    client, runs, references, root, calls = wap_api
    _write_report(root, runs[0])
    prod = client.get("/api/v1/wap/runs?env=prod")
    assert prod.status_code == 200
    (item,) = prod.json()["items"]
    assert item["staging_ref"] == "pipeline-run-prod-one"
    assert item["branch_hash"] == "p1"
    assert item["branch_state"] == "present"
    assert item["status"] == "SUCCESS"
    assert item["lifecycle_status"] == "checks_failed"
    assert item["report_state"] == "verified"
    assert "stage-one" not in prod.text and "orphan" not in prod.text
    stage = client.get("/api/v1/wap/runs?env=staging").json()
    assert [item["logical_run_id"] for item in stage["items"]] == ["stage-one"]
    assert calls["graphql"][0][1] == {"limit": 500}
    assert all(method == "GET" for method, _ in calls["requests"])


@pytest.mark.parametrize("damage", ["ref", "logical_id", "origin", "duplicate_tag"])
def test_inconsistent_run_identity_fails_closed(wap_api, damage):
    client, runs, _, _, _ = wap_api
    row = runs[0]
    if damage == "ref":
        row["tags"][2]["value"] = "pipeline-run-other"
    elif damage == "logical_id":
        row["tags"][0]["value"] = "../../secrets"
    elif damage == "origin":
        row["repositoryOrigin"] = None
    else:
        row["tags"].append(dict(row["tags"][0]))
    result = client.get("/api/v1/wap/runs?env=prod")
    assert result.status_code == 502
    assert "pipeline-run-prod-one" not in result.text


@pytest.mark.parametrize("damage", ["digest", "physical_id", "report_tags", "json"])
def test_invalid_report_never_establishes_promotion(wap_api, damage):
    client, runs, _, root, _ = wap_api
    launch = _write_report(root, runs[0], status="promoted")
    report_path = root / ".phlo" / "wap-reports" / "prod-one.json"
    report = json.loads(report_path.read_text())
    if damage == "digest":
        launch.write_text(launch.read_text() + " ")
    elif damage == "physical_id":
        report["dagster_run_id"] = "foreign-run"
        report_path.write_text(json.dumps(report))
    elif damage == "report_tags":
        report["launch_tags"]["phlo/project_id"] = "foreign-project"
        report_path.write_text(json.dumps(report))
    else:
        report_path.write_text("not json")
    (item,) = client.get("/api/v1/wap/runs?env=prod").json()["items"]
    assert item["report_state"] == "invalid"
    assert item["lifecycle_status"] is None
    assert item["reported_at"] is None
    assert item["branch_state"] == "present"


def test_snapshot_namespace_does_not_claim_a_nessie_branch(wap_api):
    client, runs, references, root, _ = wap_api
    _write_report(root, runs[0], strategy="snapshot", status="candidates_staged")
    references["references"] = []
    (item,) = client.get("/api/v1/wap/runs?env=prod").json()["items"]
    assert item["strategy"] == "snapshot"
    assert item["branch_state"] == "not_applicable"
    assert item["branch_hash"] is None
    assert item["lifecycle_status"] == "candidates_staged"


def test_missing_report_and_catalog_are_unknown_not_published(wap_api, monkeypatch):
    client, _, _, _, _ = wap_api

    async def unavailable(*args, **kwargs):
        raise BackendUnavailableError()

    monkeypatch.setattr(v1_wap, "_nessie", unavailable)
    result = client.get("/api/v1/wap/runs?env=prod").json()
    assert result["catalog_available"] is False
    (item,) = result["items"]
    assert item["report_state"] == "missing"
    assert item["strategy"] == "unknown"
    assert item["branch_state"] == "unknown"
    assert item["lifecycle_status"] is None


def test_paginated_catalog_does_not_establish_branch_absence(wap_api):
    client, runs, references, root, _ = wap_api
    _write_report(root, runs[0])
    references.update(references=[], hasMore=True)
    result = client.get("/api/v1/wap/runs?env=prod").json()
    assert result["catalog_available"] is False
    assert result["items"][0]["branch_state"] == "unknown"


def test_scan_boundary_is_reported_and_empty_is_not_provider_failure(wap_api):
    client, runs, _, _, _ = wap_api
    runs.clear()
    result = client.get("/api/v1/wap/runs?env=prod").json()
    assert result["items"] == []
    assert result["scanned_runs"] == 0
    assert result["scan_limited"] is False
    runs.extend(_run(str(index), "foreign_loc") for index in range(500))
    result = client.get("/api/v1/wap/runs?env=prod").json()
    assert result["items"] == []
    assert result["scanned_runs"] == 500
    assert result["scan_limited"] is True


def test_explicit_env_and_read_scope_are_required(wap_api, monkeypatch):
    client, _, _, _, _ = wap_api
    assert client.get("/api/v1/wap/runs").status_code == 422
    assert client.get("/api/v1/wap/runs?env=production").status_code == 422
    assert client.get("/api/v1/wap/runs?env=prod&env=staging").status_code == 422
    assert client.get("/api/v1/wap/runs?env=prod&ref=pipeline-run-orphan").status_code == 400
    from fastapi import HTTPException

    def deny(request, scope):
        assert scope == "lakehouse:read"
        raise HTTPException(403, "Denied")

    monkeypatch.setattr(v1_wap, "require_scope", deny)
    assert client.get("/api/v1/wap/runs?env=prod").status_code == 403


def test_real_launch_report_writer_is_compatible(wap_api):
    from phlo_dagster.wap_launch import WapLaunch, write_wap_report

    client, runs, _, _, _ = wap_api
    launch = WapLaunch(
        logical_run_id="prod-one",
        branch="pipeline-run-prod-one",
        catalog=None,
        created_branch=False,
        source_hash="initial-source",
        target_hash_before="initial-main",
        project_id="project",
        attempt=1,
        catalog_system="nessie",
    )
    runs[0]["tags"] = [{"key": key, "value": value} for key, value in launch.tags.items()]
    assert launch.record_launch_result(status="launched", dagster_run_id="dagster-prod-one")
    item = client.get("/api/v1/wap/runs?env=prod").json()["items"][0]
    assert item["report_state"] == "verified"
    assert item["lifecycle_status"] == "launched"
    assert item["strategy"] == "branch"
    assert write_wap_report("prod-one", status="promoted")
    item = client.get("/api/v1/wap/runs?env=prod").json()["items"][0]
    assert item["lifecycle_status"] == "promoted"
