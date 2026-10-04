"""Focused contracts for environment-scoped v1 job and run reads."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from phlo_api.api import v1_jobs
from phlo_api.errors import PhloApiError, error_envelope


def _run(
    run_id: str,
    location: str,
    ref: str | None,
    *,
    status: str = "SUCCESS",
    start: float = 10,
    end: float = 20,
) -> dict:
    tags = [] if ref is None else [{"key": "phlo/ref", "value": ref}]
    return {
        "runId": run_id,
        "pipelineName": "orders",
        "status": status,
        "creationTime": 1,
        "startTime": start,
        "endTime": end,
        "assetSelection": [{"path": [location, "orders"]}],
        "tags": tags,
        "repositoryOrigin": {"repositoryLocationName": location},
    }


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        json.dumps(
            {
                "prod": {"dagster_location": "prod_loc", "nessie_ref": "main"},
                "staging": {"dagster_location": "stage_loc", "nessie_ref": "candidate"},
            }
        ),
    )
    state = {
        "runs": [
            _run("prod-run", "prod_loc", "main"),
            _run("stage-run", "stage_loc", "candidate"),
            _run("wrong-ref", "prod_loc", "candidate"),
        ],
        "schedule_status": {"prod_loc": "RUNNING", "stage_loc": "RUNNING"},
        "calls": [],
    }

    async def graphql(url, query, variables=None):
        state["calls"].append((query, variables))
        if "V1Jobs" in query:
            return {
                "data": {
                    "repositoriesOrError": {
                        "__typename": "RepositoryConnection",
                        "nodes": [
                            {
                                "name": "repo",
                                "location": {"name": location},
                                "pipelines": [
                                    {
                                        "name": "orders",
                                        "description": f"{location} orders",
                                    }
                                ],
                                "schedules": [
                                    {
                                        "id": f"{location}-daily-state",
                                        "name": "daily",
                                        "pipelineName": "orders",
                                        "scheduleState": {
                                            "id": f"{location}-daily-state",
                                            "status": state["schedule_status"][location],
                                        },
                                    }
                                ],
                            }
                            for location in ("prod_loc", "stage_loc")
                        ],
                    }
                }
            }
        if "V1AssetJobs" in query:
            return {
                "data": {
                    "assetNodes": [
                        {
                            "assetKey": {"path": [location, "orders"]},
                            "jobNames": ["orders"],
                            "repository": {"location": {"name": location}},
                        }
                        for location in ("prod_loc", "stage_loc")
                    ]
                }
            }
        if "V1ScheduleStop" in query:
            state["schedule_status"]["prod_loc"] = "STOPPED"
            return {
                "data": {
                    "stopRunningSchedule": {
                        "__typename": "ScheduleStateResult",
                        "scheduleState": {"status": "STOPPED"},
                    }
                }
            }
        if "V1LaunchJob" in query:
            state["launch_variables"] = variables
            return {
                "data": {
                    "launchPipelineExecution": {
                        "__typename": "LaunchRunSuccess",
                        "run": {"runId": "launched-run", "status": "STARTED"},
                    }
                }
            }
        if "V1Run(" in query:
            row = next(row for row in state["runs"] if row["runId"] == variables["runId"])
            return {
                "data": {
                    "runOrError": {
                        "__typename": "Run",
                        **row,
                        "eventConnection": {
                            "events": [
                                {
                                    "__typename": "RunStartEvent",
                                    "eventType": "RUN_START",
                                    "message": "started",
                                    "timestamp": "1000",
                                    "stepKey": None,
                                },
                                {
                                    "__typename": "RunSuccessEvent",
                                    "eventType": "RUN_SUCCESS",
                                    "message": "finished",
                                    "timestamp": "2000",
                                    "stepKey": None,
                                },
                            ][: variables["eventLimit"]],
                            "cursor": "cursor-1"
                            if variables["afterCursor"] is None
                            else "cursor-2",
                            "hasMore": variables["afterCursor"] is None,
                        },
                    }
                }
            }
        return {"data": {"runsOrError": {"__typename": "Runs", "results": state["runs"]}}}

    monkeypatch.setattr(v1_jobs, "graphql_request", graphql)
    app = FastAPI()
    app.include_router(v1_jobs.router, prefix="/api/v1")

    @app.exception_handler(PhloApiError)
    async def api_error(request: Request, exc: PhloApiError):
        return JSONResponse(error_envelope(exc), status_code=exc.status_code)

    with TestClient(app) as client:
        yield client, state, monkeypatch


def test_jobs_schedules_and_runs_are_distinct_by_environment(api):
    client, _, _ = api
    prod_job = client.get("/api/v1/jobs?env=prod").json()["items"][0]
    stage_job = client.get("/api/v1/jobs?env=staging").json()["items"][0]
    assert prod_job["description"] == "prod_loc orders"
    assert stage_job["description"] == "stage_loc orders"
    assert prod_job["selected_assets"] == [["prod_loc", "orders"]]
    assert prod_job["assets_url"] == "/api/v1/assets?env=prod"
    assert prod_job["incidents_url"] == "/api/v1/incidents?env=prod"
    assert client.get("/api/v1/jobs/orders/schedules?env=prod").json()["items"][0]["id"] == "daily"
    assert [item["run_id"] for item in client.get("/api/v1/runs?env=prod").json()["items"]] == [
        "prod-run"
    ]
    assert [item["run_id"] for item in client.get("/api/v1/runs?env=staging").json()["items"]] == [
        "stage-run"
    ]


def test_wrong_location_and_ref_do_not_leak_and_missing_ref_fails_closed(api):
    client, state, _ = api
    assert client.get("/api/v1/runs/stage-run?env=prod").status_code == 404
    assert client.get("/api/v1/runs/wrong-ref?env=prod").status_code == 404
    state["runs"] = [_run("unverified", "prod_loc", None)]
    response = client.get("/api/v1/runs?env=prod")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "backend_unavailable"
    duplicate = _run("duplicate", "prod_loc", "main")
    duplicate["tags"].append({"key": "phlo/ref", "value": "main"})
    state["runs"] = [duplicate]
    assert client.get("/api/v1/runs?env=prod").status_code == 503


def test_malformed_and_outage_responses_are_not_empty_success(api):
    client, _, monkeypatch = api

    async def malformed(*args, **kwargs):
        return {"data": {"runsOrError": {"__typename": "PythonError"}}}

    monkeypatch.setattr(v1_jobs, "graphql_request", malformed)
    assert client.get("/api/v1/runs?env=prod").status_code == 502

    async def outage(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(v1_jobs, "graphql_request", outage)
    assert client.get("/api/v1/jobs?env=prod").status_code == 503


def test_scans_and_responses_are_bounded(api):
    client, state, _ = api
    assert client.get("/api/v1/runs?env=prod&limit=101").status_code == 422
    client.get("/api/v1/jobs/orders/patterns?env=prod")
    assert state["calls"][-1][1] == {"limit": 200}
    state["runs"] = [_run(str(index), "prod_loc", "main") for index in range(201)]
    assert client.get("/api/v1/jobs/orders/patterns?env=prod").status_code == 502


def test_timeline_logs_and_patterns_are_derived_from_scoped_runs(api):
    client, state, _ = api
    timeline = client.get("/api/v1/runs/prod-run/timeline?env=prod&limit=1")
    assert timeline.status_code == 200
    assert timeline.json()["truncated"] is True
    assert timeline.json()["items"][0]["event_type"] == "RUN_START"
    logs = client.get("/api/v1/runs/prod-run/logs?env=prod&limit=2").json()
    assert logs["follow_supported"] is True
    assert logs["next_cursor"] == "cursor-1"
    assert logs["status"] == "SUCCESS"
    assert logs["is_terminal"] is True
    assert [item["message"] for item in logs["items"]] == ["started", "finished"]
    continued = client.get(
        "/api/v1/runs/prod-run/logs?env=prod&after_cursor=cursor-1&limit=1"
    ).json()
    assert continued["next_cursor"] == "cursor-2"
    assert continued["truncated"] is False

    state["runs"] = [
        _run("failed", "prod_loc", "main", status="FAILURE"),
        _run("normal", "prod_loc", "main", start=10, end=20),
        _run("normal-2", "prod_loc", "main", start=10, end=22),
        _run("slow", "prod_loc", "main", start=10, end=210),
    ]
    patterns = client.get("/api/v1/jobs/orders/patterns?env=prod").json()["items"]
    assert [(item["kind"], item["run_ids"]) for item in patterns] == [
        ("failure", ["failed"]),
        ("slow_run", ["slow"]),
    ]


def test_job_summary_histogram_and_maintenance_windows_are_explicitly_scoped(api, monkeypatch):
    client, state, _ = api
    state["runs"] = [
        _run("short", "prod_loc", "main", start=10, end=20),
        _run("medium", "prod_loc", "main", status="FAILURE", start=10, end=120),
        _run("stage", "stage_loc", "candidate", start=10, end=1200),
    ]
    summary = client.get("/api/v1/jobs/orders/summary?env=prod").json()
    assert summary["counts_by_status"] == {"SUCCESS": 1, "FAILURE": 1}
    assert summary["duration_histogram_seconds"] == {
        "<60": 1,
        "60-299": 1,
        "300-899": 0,
        ">=900": 0,
    }
    assert client.get("/api/v1/maintenance-windows?env=prod").json() == {
        "env": "prod",
        "status": "unavailable",
        "items": [],
    }
    monkeypatch.setenv(
        "PHLO_V1_MAINTENANCE_WINDOWS",
        json.dumps(
            {
                "prod": [
                    {
                        "id": "quarterly-upgrade",
                        "starts_at": "2026-10-02T01:00:00Z",
                        "ends_at": "2026-10-02T03:00:00Z",
                        "description": "Production maintenance",
                    }
                ],
                "staging": [],
            }
        ),
    )
    assert client.get("/api/v1/maintenance-windows?env=prod").json()["items"][0]["id"] == (
        "quarterly-upgrade"
    )
    assert client.get("/api/v1/maintenance-windows?env=staging").json()["items"] == []


def test_actions_require_authorization_and_single_replica_gate(api, monkeypatch):
    client, _, _ = api
    from phlo_api.api import operation_controls

    def denied(request, required_scope):
        raise HTTPException(status_code=403, detail="denied")

    monkeypatch.setattr(operation_controls, "require_scope", denied)
    body = {
        "idempotency_key": "denied-schedule",
        "expected_status": "RUNNING",
        "confirmed": True,
    }
    assert client.post("/api/v1/schedules/daily/pause?env=prod", json=body).status_code == 403

    monkeypatch.setattr(
        operation_controls,
        "require_scope",
        lambda request, required_scope: {"subject": "operator", "scopes": [required_scope]},
    )
    assert client.post("/api/v1/schedules/daily/pause?env=prod", json=body).status_code == 503


def test_unconfirmed_schedule_action_does_not_consume_idempotency_key(api, monkeypatch, tmp_path):
    client, state, _ = api
    from phlo_api.api import operation_controls

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_V1_ACTIONS_SINGLE_REPLICA", "1")
    monkeypatch.setenv("PHLO_V1_ACTIONS_SINGLE_PROCESS", "1")
    monkeypatch.setenv("PHLO_V1_ACTIONS_REF_TAG_CONTRACT", "1")
    monkeypatch.setattr(
        operation_controls,
        "require_scope",
        lambda request, required_scope: {"subject": "operator", "scopes": [required_scope]},
    )
    body = {
        "idempotency_key": "confirm-schedule-pause",
        "expected_status": "RUNNING",
        "confirmed": False,
    }
    denied = client.post("/api/v1/schedules/daily/pause?env=prod", json=body)
    assert denied.status_code == 422
    assert state["calls"] == []

    confirmed = client.post(
        "/api/v1/schedules/daily/pause?env=prod", json={**body, "confirmed": True}
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["result"]["schedule_status"] == "STOPPED"


def test_schedule_pause_replay_skips_stale_state_and_audits_once(api, monkeypatch, tmp_path):
    client, state, _ = api
    from phlo_api.api import operation_controls

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_V1_ACTIONS_SINGLE_REPLICA", "1")
    monkeypatch.setenv("PHLO_V1_ACTIONS_SINGLE_PROCESS", "1")
    monkeypatch.setenv("PHLO_V1_ACTIONS_REF_TAG_CONTRACT", "1")
    monkeypatch.setattr(
        operation_controls,
        "require_scope",
        lambda request, required_scope: {"subject": "operator", "scopes": [required_scope]},
    )
    body = {
        "idempotency_key": "pause-production-daily",
        "expected_status": "RUNNING",
        "confirmed": True,
    }
    first = client.post("/api/v1/schedules/daily/pause?env=prod", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["result"]["schedule_status"] == "STOPPED"
    call_count = len(state["calls"])
    replay = client.post("/api/v1/schedules/daily/pause?env=prod", json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert len(state["calls"]) == call_count
    stale = client.post(
        "/api/v1/schedules/daily/pause?env=prod",
        json={**body, "idempotency_key": "pause-production-daily-stale"},
    )
    assert stale.status_code == 409
    assert len(state["calls"]) == call_count + 2
    audit = tmp_path / ".phlo/audit/operations.jsonl"
    records = [json.loads(line) for line in audit.read_text().splitlines()]
    assert [record["operation"] for record in records] == ["v1_schedule_action"]


def test_job_launch_is_ref_pinned_and_idempotently_replayed(api, monkeypatch, tmp_path):
    client, state, _ = api
    from phlo_api.api import operation_controls

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_V1_ACTIONS_SINGLE_REPLICA", "1")
    monkeypatch.setenv("PHLO_V1_ACTIONS_SINGLE_PROCESS", "1")
    monkeypatch.setenv("PHLO_V1_ACTIONS_REF_TAG_CONTRACT", "1")
    monkeypatch.setattr(
        operation_controls,
        "require_scope",
        lambda request, required_scope: {"subject": "operator", "scopes": [required_scope]},
    )
    body = {
        "idempotency_key": "launch-prod-orders",
        "dry_run": False,
        "confirmed": True,
    }
    first = client.post("/api/v1/jobs/orders/launch?env=prod", json=body)
    assert first.status_code == 200, first.text
    variables = state["launch_variables"]["executionParams"]
    assert variables["selector"]["repositoryLocationName"] == "prod_loc"
    assert {tag["key"]: tag["value"] for tag in variables["executionMetadata"]["tags"]}[
        "phlo/ref"
    ] == "main"
    call_count = len(state["calls"])
    replay = client.post("/api/v1/jobs/orders/launch?env=prod", json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert len(state["calls"]) == call_count


def test_retry_replay_survives_status_change_without_reinvoking_provider(
    api, monkeypatch, tmp_path
):
    client, state, _ = api
    from phlo_api.api import operation_controls
    from phlo_api.observatory_api import orchestrator_operations

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_V1_ACTIONS_SINGLE_REPLICA", "1")
    monkeypatch.setenv("PHLO_V1_ACTIONS_SINGLE_PROCESS", "1")
    monkeypatch.setenv("PHLO_V1_ACTIONS_REF_TAG_CONTRACT", "1")
    monkeypatch.setattr(
        operation_controls,
        "require_scope",
        lambda request, required_scope: {"subject": "operator", "scopes": [required_scope]},
    )
    state["runs"] = [_run("failed-run", "prod_loc", "main", status="FAILURE")]
    provider_calls = []

    class Provider:
        async def retry_run(self, run_id, payload):
            provider_calls.append((run_id, payload))
            state["runs"][0]["status"] = "STARTED"
            return {"accepted": True, "run_id": "retry-run", "status": "QUEUED"}

    monkeypatch.setattr(
        orchestrator_operations, "resolve_orchestrator_operations", lambda: Provider()
    )
    body = {
        "idempotency_key": "retry-failed-run",
        "expected_status": "FAILURE",
        "dry_run": False,
        "confirmed": True,
    }
    first = client.post("/api/v1/runs/failed-run/retry?env=prod", json=body)
    assert first.status_code == 200, first.text
    call_count = len(state["calls"])
    replay = client.post("/api/v1/runs/failed-run/retry?env=prod", json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert len(provider_calls) == 1
    assert len(state["calls"]) == call_count
