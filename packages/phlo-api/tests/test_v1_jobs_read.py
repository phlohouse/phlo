"""Focused contracts for environment-scoped v1 job and run reads."""

from __future__ import annotations

import asyncio
import base64
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


def _repository_asset_response(nodes):
    identities = {
        (node["repository"]["name"], node["repository"]["location"]["name"]) for node in nodes
    }
    return {
        "data": {
            "repositoriesOrError": {
                "__typename": "RepositoryConnection",
                "nodes": [
                    {
                        "name": repo,
                        "location": {"name": location},
                        "assetNodes": [
                            node
                            for node in nodes
                            if (node["repository"]["name"], node["repository"]["location"]["name"])
                            == (repo, location)
                        ],
                    }
                    for repo, location in sorted(identities)
                ],
            }
        }
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
                            "repository": {"name": "repo", "location": {"name": location}},
                        }
                        for location in ("prod_loc", "stage_loc")
                        if not variables
                        or variables["pipeline"]["repositoryLocationName"] == location
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
        rows = state["runs"]
        filters = variables.get("filter", {})
        if "pipelineName" in filters:
            rows = [row for row in rows if row["pipelineName"] == filters["pipelineName"]]
        if "statuses" in filters:
            rows = [row for row in rows if row["status"] in filters["statuses"]]
        if variables.get("cursor"):
            offset = (
                next(index for index, row in enumerate(rows) if row["runId"] == variables["cursor"])
                + 1
            )
            rows = rows[offset:]
        return {
            "data": {"runsOrError": {"__typename": "Runs", "results": rows[: variables["limit"]]}}
        }

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


def test_run_trigger_tags_do_not_expose_arbitrary_definition_values(api):
    client, state, _ = api
    state["runs"][0]["tags"].extend(
        [
            {"key": "dagster/schedule_name", "value": "daily"},
            {"key": "private-config", "value": "not-for-the-run-view"},
        ]
    )
    run = client.get("/api/v1/runs/prod-run?env=prod").json()
    assert run["tags"] == {"dagster/schedule_name": "daily"}


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
    assert state["calls"][-1][1] == {
        "limit": 200,
        "cursor": None,
        "filter": {"tags": [{"key": "phlo/ref", "value": "main"}], "pipelineName": "orders"},
    }
    state["runs"] = [_run(str(index), "prod_loc", "main") for index in range(201)]
    assert client.get("/api/v1/jobs/orders/patterns?env=prod").json()["scanned_runs"] == 200


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


def test_timeline_requests_common_fields_for_engine_and_materialization_events(api):
    client, _, monkeypatch = api

    async def graphql(url, query, variables=None):
        assert "... on MessageEvent { eventType message timestamp stepKey level }" in query
        return {
            "data": {
                "runOrError": {
                    "__typename": "Run",
                    **_run("prod-run", "prod_loc", "main"),
                    "eventConnection": {
                        "events": [
                            {
                                "__typename": typename,
                                "eventType": event_type,
                                "message": message,
                                "timestamp": timestamp,
                                "stepKey": step,
                            }
                            for typename, event_type, message, timestamp, step in (
                                ("EngineEvent", "ENGINE_EVENT", "worker started", "1250", None),
                                (
                                    "MaterializationEvent",
                                    "ASSET_MATERIALIZATION",
                                    "table written",
                                    "2750",
                                    "orders",
                                ),
                            )
                        ],
                        "cursor": "events-end",
                        "hasMore": False,
                    },
                }
            }
        }

    monkeypatch.setattr(v1_jobs, "graphql_request", graphql)
    for endpoint in ("timeline", "logs"):
        response = client.get(f"/api/v1/runs/prod-run/{endpoint}?env=prod&limit=2")
        assert response.status_code == 200
        assert [item["event_type"] for item in response.json()["items"]] == [
            "ENGINE_EVENT",
            "ASSET_MATERIALIZATION",
        ]
        assert [item["timestamp"] for item in response.json()["items"]] == [
            "1970-01-01T00:00:01.250000Z",
            "1970-01-01T00:00:02.750000Z",
        ]
        assert response.json()["items"][1]["step_key"] == "orders"


def test_failure_diagnostics_and_captured_logs_are_returned(api, monkeypatch):
    client, _, _ = api
    multibyte_stdout = "é" * 131_072 + "a"
    error = {
        "message": "DagsterInvariantViolationError: partition key unavailable",
        "className": "DagsterInvariantViolationError",
        "stack": ['  File "execute_plan.py", line 243\n'] * 7,
        "causes": [],
    }
    calls = []

    async def graphql(url, query, variables=None):
        calls.append((query, variables))
        if "V1RunCapturedLogs" in query:
            assert variables == {"runId": "prod-run", "fileKey": "captured-key"}
            return {
                "data": {
                    "runOrError": {
                        "__typename": "Run",
                        "capturedLogs": {
                            "stdout": multibyte_stdout,
                            "stderr": "worker error",
                        },
                    }
                }
            }
        assert "ExecutionStepFailureEvent" in query
        return {
            "data": {
                "runOrError": {
                    "__typename": "Run",
                    **_run("prod-run", "prod_loc", "main", status="FAILURE"),
                    "eventConnection": {
                        "events": [
                            {
                                "__typename": "ExecutionStepFailureEvent",
                                "eventType": "STEP_FAILURE",
                                "message": "step failed",
                                "timestamp": "1000",
                                "stepKey": "orders",
                                "level": "ERROR",
                                "error": error,
                            },
                            {
                                "__typename": "LogsCapturedEvent",
                                "eventType": "LOGS_CAPTURED",
                                "message": "captured",
                                "timestamp": "2000",
                                "stepKey": None,
                                "level": "DEBUG",
                                "fileKey": "captured-key",
                                "stepKeys": ["orders"],
                            },
                        ],
                        "cursor": "end",
                        "hasMore": False,
                    },
                }
            }
        }

    monkeypatch.setattr(v1_jobs, "graphql_request", graphql)
    response = client.get("/api/v1/runs/prod-run/logs?env=prod&limit=10")
    assert response.status_code == 200, response.text
    payload = response.json()
    failure = payload["items"][0]
    assert failure["error"]["class_name"] == "DagsterInvariantViolationError"
    assert len(failure["error"]["stack"]) == 7
    assert payload["captured_logs_available"] is True
    captured = payload["captured_logs"][0]
    assert captured["file_key"] == "captured-key"
    assert captured["step_keys"] == ["orders"]
    assert len(captured["stdout"].encode("utf-8")) == 256 * 1024
    assert captured["stdout"] == "é" * 131_072
    assert captured["stderr"] == "worker error"
    assert captured["available"] is True
    assert captured["truncated"] is True
    assert len(calls) == 2


def test_run_diagnostics_queries_match_installed_dagster_schema():
    from graphql import parse, validate

    from dagster_graphql.schema import create_schema

    schema = create_schema().graphql_schema
    for query in (v1_jobs.RUN_QUERY, v1_jobs.RUN_CAPTURED_LOGS_QUERY):
        assert validate(schema, parse(query)) == []


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
        "partition_key": "2026-08-20",
    }
    first = client.post("/api/v1/jobs/orders/launch?env=prod", json=body)
    assert first.status_code == 200, first.text
    variables = state["launch_variables"]["executionParams"]
    assert variables["selector"]["repositoryLocationName"] == "prod_loc"
    tags = {tag["key"]: tag["value"] for tag in variables["executionMetadata"]["tags"]}
    assert tags["phlo/ref"] == "main"
    assert tags["dagster/partition"] == "2026-08-20"
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


def test_paginated_history_crosses_100_rows_and_empty_environment_pages(api):
    client, state, _ = api
    state["runs"] = [
        *[_run(f"stage-{index}", "stage_loc", "candidate") for index in range(125)],
        *[_run(f"prod-{index}", "prod_loc", "main") for index in range(137)],
    ]
    cursor = None
    observed = []
    pages = 0
    while True:
        params = {"env": "prod", "limit": 100, "job_id": "orders"}
        if cursor:
            params["cursor"] = cursor
        page = client.get("/api/v1/runs", params=params).json()
        if pages == 0:
            assert page["items"] == []
            assert page["next_cursor"]
        observed.extend(item["run_id"] for item in page["items"])
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages == 3
    assert observed == [f"prod-{index}" for index in range(137)]
    assert client.get("/api/v1/jobs/orders/summary?env=prod").json()["scanned_runs"] == 137


def test_cursors_reject_environment_filter_and_mapping_changes(api):
    client, state, monkeypatch = api
    state["runs"] = [_run(f"prod-{index}", "prod_loc", "main") for index in range(101)]
    cursor = client.get("/api/v1/runs?env=prod&job_id=orders").json()["next_cursor"]
    for params in (
        {"env": "staging", "job_id": "orders"},
        {"env": "prod", "job_id": "other"},
        {"env": "prod", "job_id": "orders", "status": "FAILURE"},
    ):
        assert client.get("/api/v1/runs", params={**params, "cursor": cursor}).status_code == 422
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        json.dumps(
            {
                "prod": {"dagster_location": "prod_loc", "nessie_ref": "next"},
                "staging": {"dagster_location": "stage_loc", "nessie_ref": "candidate"},
            }
        ),
    )
    assert (
        client.get(
            "/api/v1/runs", params={"env": "prod", "job_id": "orders", "cursor": cursor}
        ).status_code
        == 422
    )
    for invalid in (
        "invalid",
        base64.urlsafe_b64encode(b"[]").decode(),
        base64.urlsafe_b64encode(b'"text"').decode(),
    ):
        assert (
            client.get("/api/v1/runs", params={"env": "prod", "cursor": invalid}).status_code == 422
        )


def test_job_metadata_uses_definitions_not_repository_names(api):
    client, _, monkeypatch = api
    original = v1_jobs.graphql_request

    async def metadata(url, query, variables=None):
        result = await original(url, query, variables)
        if "V1AssetJobs" in query:
            for node in result["data"]["assetNodes"]:
                node.update(
                    groupName="telemetry",
                    metadataEntries=[
                        {"label": "owner", "text": "fleet-operations"},
                        {"label": "source_name", "text": "sensor-stream"},
                        {"label": "consumers", "jsonString": '[{"name":"Batch release"}]'},
                    ],
                )
        return result

    monkeypatch.setattr(v1_jobs, "graphql_request", metadata)
    job = client.get("/api/v1/jobs?env=prod").json()["items"][0]
    assert (job["domain"], job["owners"], job["source"], job["feeds_batch_release"]) == (
        "telemetry",
        ["fleet-operations"],
        "sensor-stream",
        True,
    )
    monkeypatch.setattr(v1_jobs, "graphql_request", original)
    job = client.get("/api/v1/jobs?env=prod").json()["items"][0]
    assert job["domain"] is None and job["source"] is None and job["owners"] == []


def test_job_membership_uses_exact_repository_and_location_not_workspace_winner(api):
    client, _, monkeypatch = api
    selectors = []

    async def graphql(url, query, variables=None):
        if "V1Jobs" in query:
            return {
                "data": {
                    "repositoriesOrError": {
                        "__typename": "RepositoryConnection",
                        "nodes": [
                            {
                                "name": repo,
                                "location": {"name": location},
                                "pipelines": [
                                    {"name": name, "description": None}
                                    for name in ("orders", "__ASSET_JOB")
                                ],
                                "schedules": [],
                            }
                            for repo, location in (
                                ("first", "prod_loc"),
                                ("second", "prod_loc"),
                                ("first", "stage_loc"),
                            )
                        ],
                    }
                }
            }
        selector = (variables or {}).get("pipeline")
        if selector:
            selectors.append(selector)
            repo = selector["repositoryName"]
            location = selector["repositoryLocationName"]
            paths = [[repo, "orders"]]
            if selector["pipelineName"] == "__ASSET_JOB":
                paths.append([repo, "other"])
        else:
            # Global inventory chose the other environment's copy of the same key.
            repo, location, paths = "first", "stage_loc", [["first", "orders"]]
        return {
            "data": {
                "assetNodes": [
                    {
                        "assetKey": {"path": path},
                        "jobNames": [],
                        "repository": {"name": repo, "location": {"name": location}},
                    }
                    for path in paths
                ]
            }
        }

    monkeypatch.setattr(v1_jobs, "graphql_request", graphql)
    response = client.get("/api/v1/jobs?env=prod")
    assert response.status_code == 200
    jobs = {(job["repository_name"], job["id"]): job for job in response.json()["items"]}
    assert jobs["first", "orders"]["selected_assets"] == [["first", "orders"]]
    assert jobs["second", "orders"]["selected_assets"] == [["second", "orders"]]
    assert jobs["first", "__ASSET_JOB"]["selected_assets"] == [
        ["first", "orders"],
        ["first", "other"],
    ]
    assert {selector["repositoryLocationName"] for selector in selectors} == {"prod_loc"}

    for wrong_origin in ({"name": "unrelated"}, {"location": {"name": "stage_loc"}}):

        async def out_of_scope(url, query, variables=None):
            result = await graphql(url, query, variables)
            if "V1AssetJobs" in query:
                result["data"]["assetNodes"][0]["repository"].update(wrong_origin)
            return result

        monkeypatch.setattr(v1_jobs, "graphql_request", out_of_scope)
        assert client.get("/api/v1/jobs?env=prod").status_code == 502


def test_real_dagster_job_membership_survives_overlapping_location_asset_keys(
    tmp_path, monkeypatch
):
    import dagster as dg
    from dagster._core.workspace.context import WorkspaceProcessContext
    from dagster._core.workspace.load_target import WorkspaceFileTarget
    from dagster_graphql.test.utils import async_execute_dagster_graphql
    from phlo_api.api import operation_controls, v1_assets

    definitions = tmp_path / "definitions.py"
    definitions.write_text("""
import dagster as dg

@dg.asset(key=["shared", "source"])
def source():
    return 1

@dg.asset(key=["shared", "report"], deps=[source], group_name="transform", metadata={"phlo/layer": "gold"})
def report():
    return 2

@dg.repository(name="telemetry")
def definitions():
    daily = dg.define_asset_job("daily", selection=dg.AssetSelection.assets(report))
    return [source, report,
            dg.define_asset_job("hourly", selection=dg.AssetSelection.assets(source)),
            daily, dg.ScheduleDefinition(name="gold_daily", job=daily, cron_schedule="0 0 * * *")]
""")
    workspace = tmp_path / "workspace.yaml"
    workspace.write_text(
        "load_from:\n"
        + "".join(
            f"  - python_file:\n      relative_path: {definitions}\n      attribute: definitions\n      location_name: {location}\n"
            for location in ("prod_loc", "stage_loc")
        )
    )
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        json.dumps(
            {
                "prod": {"dagster_location": "prod_loc", "nessie_ref": "main"},
                "staging": {"dagster_location": "stage_loc", "nessie_ref": "candidate"},
            }
        ),
    )
    for gate in (
        "PHLO_V1_ACTIONS_SINGLE_REPLICA",
        "PHLO_V1_ACTIONS_SINGLE_PROCESS",
        "PHLO_V1_ACTIONS_REF_TAG_CONTRACT",
    ):
        monkeypatch.setenv(gate, "1")
    monkeypatch.setattr(
        operation_controls, "require_scope", lambda request, scope: {"subject": "operator"}
    )
    with dg.instance_for_test() as instance:
        with WorkspaceProcessContext(
            instance, WorkspaceFileTarget(paths=[str(workspace)])
        ) as process:
            with process.create_request_context() as context:

                async def graphql(url, query, variables=None):
                    result = await async_execute_dagster_graphql(context, query, variables or {})
                    assert not result.errors, result.errors
                    return result.formatted

                monkeypatch.setattr(v1_jobs, "graphql_request", graphql)
                monkeypatch.setattr(v1_assets, "graphql_request", graphql)
                for env in ("prod", "staging"):
                    result = asyncio.run(
                        v1_jobs.v1_jobs(
                            Request(
                                {
                                    "type": "http",
                                    "headers": [],
                                    "query_string": f"env={env}".encode(),
                                }
                            ),
                            env=env,
                        )
                    )
                    jobs = {job.id: job for job in result.items}
                    assert jobs["hourly"].selected_assets == [["shared", "source"]]
                    assert jobs["daily"].selected_assets == [["shared", "report"]]
                    assert sorted(jobs["__ASSET_JOB"].selected_assets) == [
                        ["shared", "report"],
                        ["shared", "source"],
                    ]
                    assert all(job.repository_name == "telemetry" for job in jobs.values())
                targets = asyncio.run(
                    v1_jobs.preflight_incident_pause(
                        Request({"type": "http", "headers": [], "query_string": b"env=staging"}),
                        env="staging",
                        asset_ids=["shared.source"],
                    )
                )
                assert [
                    (
                        target["location"],
                        target["repository_name"],
                        target["schedule_id"],
                        target["asset_ids"],
                    )
                    for target in targets
                ] == [
                    ("stage_loc", "telemetry", "gold_daily", ["shared/report"]),
                ]


def test_incident_pause_uses_explicit_gold_graph_and_preserves_actor_scope(api, tmp_path):
    _, state, monkeypatch = api
    from phlo_api.api import operation_controls, v1_assets

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    for gate in (
        "PHLO_V1_ACTIONS_SINGLE_REPLICA",
        "PHLO_V1_ACTIONS_SINGLE_PROCESS",
        "PHLO_V1_ACTIONS_REF_TAG_CONTRACT",
    ):
        monkeypatch.setenv(gate, "1")
    monkeypatch.setattr(
        operation_controls,
        "require_scope",
        lambda request, required_scope: {"subject": "operator", "scopes": [required_scope]},
    )
    original = v1_jobs.graphql_request

    async def graph(url, query, variables=None):
        result = await original(url, query, variables)
        if "V1AssetJobs" in query:
            result["data"]["assetNodes"] = [
                {
                    "assetKey": {"path": [key]},
                    "dependencyKeys": [{"path": [dep]} for dep in deps],
                    "jobNames": [job],
                    "groupName": "transform",
                    "metadataEntries": [{"label": "phlo/layer", "text": layer}],
                    "repository": {"name": "repo", "location": {"name": location}},
                }
                for location in ("prod_loc", "stage_loc")
                for key, deps, job, layer in (
                    ("source", [], "ingest", "bronze"),
                    ("middle", ["source"], "silver", "silver"),
                    ("gold-table", ["middle"], "orders", "gold"),
                    ("unrelated", [], "other", "gold"),
                )
                if not variables
                or (
                    variables["pipeline"]["repositoryLocationName"] == location
                    and variables["pipeline"]["pipelineName"] == job
                )
            ]
        return result

    async def asset_graph(url, query, variables=None):
        result = await graph(url, v1_jobs.ASSET_JOBS_QUERY)
        return _repository_asset_response(result["data"]["assetNodes"])

    monkeypatch.setattr(v1_jobs, "graphql_request", graph)
    monkeypatch.setattr(v1_assets, "graphql_request", asset_graph)
    request = Request({"type": "http", "headers": [], "query_string": b"env=prod"})
    targets = asyncio.run(
        v1_jobs.preflight_incident_pause(request, env="prod", asset_ids=["source"])
    )
    assert len(targets) == 1
    assert targets[0]["asset_ids"] == ["gold-table"]
    assert targets[0]["location"] == "prod_loc"
    outcome = asyncio.run(
        v1_jobs.pause_incident_downstream(
            request, env="prod", incident_id="incident", effect_id="effect", targets=targets
        )
    )
    assert outcome[0]["status"] == "paused"
    replay = asyncio.run(
        v1_jobs.pause_incident_downstream(
            request, env="prod", incident_id="incident", effect_id="effect", targets=targets
        )
    )
    assert replay[0]["status"] == "already_paused"
    assert state["schedule_status"]["stage_loc"] == "RUNNING"
    assert sum("V1ScheduleStop" in query for query, _ in state["calls"]) == 1
    partial = asyncio.run(
        v1_jobs.pause_incident_downstream(
            request,
            env="prod",
            incident_id="incident",
            effect_id="effect",
            targets=[{**targets[0], "env": "staging"}, targets[0]],
        )
    )
    assert [item["status"] for item in partial] == ["failed", "already_paused"]
    for changes in ({"env": "staging"}, {"subject": "another-user"}, {"schedule_id": "unrelated"}):
        outcome = asyncio.run(
            v1_jobs.pause_incident_downstream(
                request,
                env="prod",
                incident_id="incident",
                effect_id="effect",
                targets=[{**targets[0], **changes}],
            )
        )
        assert outcome[0]["status"] == "failed"

    # A cached accepted action is not proof that a later operator resume stayed paused.
    state["schedule_status"]["prod_loc"] = "RUNNING"
    resumed = asyncio.run(
        v1_jobs.pause_incident_downstream(
            request, env="prod", incident_id="incident", effect_id="effect", targets=targets
        )
    )
    assert resumed[0]["status"] == "failed"
    assert "not paused" in resumed[0]["error"]
    assert sum("V1ScheduleStop" in query for query, _ in state["calls"]) == 1

    def denied(request, required_scope):
        raise HTTPException(status_code=403, detail="denied")

    monkeypatch.setattr(operation_controls, "require_scope", denied)
    with pytest.raises(HTTPException):
        asyncio.run(v1_jobs.preflight_incident_pause(request, env="prod", asset_ids=["source"]))


def test_incident_preflight_resolves_inventory_aliases_without_splitting_dotted_names(api):
    _, _, monkeypatch = api
    from phlo_api.api import operation_controls, v1_assets
    from phlo_api.errors import ConflictError

    for gate in (
        "PHLO_V1_ACTIONS_SINGLE_REPLICA",
        "PHLO_V1_ACTIONS_SINGLE_PROCESS",
        "PHLO_V1_ACTIONS_REF_TAG_CONTRACT",
    ):
        monkeypatch.setenv(gate, "1")
    monkeypatch.setattr(
        operation_controls,
        "require_scope",
        lambda request, required_scope: {"subject": "operator", "scopes": [required_scope]},
    )
    original = v1_jobs.graphql_request
    nodes = [
        {
            "assetKey": {"path": path},
            "dependencyKeys": [{"path": ["bronze", "orders"]}] if gold else [],
            "jobNames": ["orders"] if gold else ["ingest"],
            "metadataEntries": [{"label": "phlo/layer", "text": "gold" if gold else "bronze"}],
            "repository": {"name": "repo", "location": {"name": "prod_loc"}},
        }
        for path, gold in ((["bronze", "orders"], False), (["gold", "orders"], True))
    ]

    async def graph(url, query, variables=None):
        if "V1AssetJobs" in query:
            return {"data": {"assetNodes": nodes}}
        return await original(url, query, variables)

    async def asset_graph(url, query, variables=None):
        return _repository_asset_response(nodes)

    monkeypatch.setattr(v1_jobs, "graphql_request", graph)
    monkeypatch.setattr(v1_assets, "graphql_request", asset_graph)
    request = Request({"type": "http", "headers": [], "query_string": b"env=prod"})

    def preflight(asset_id):
        return asyncio.run(
            v1_jobs.preflight_incident_pause(request, env="prod", asset_ids=[asset_id])
        )

    dotted = preflight("bronze.orders")
    assert dotted == preflight("bronze/orders")
    assert dotted[0]["affected_asset_ids"] == ["bronze/orders"]
    assert dotted[0]["asset_ids"] == ["gold/orders"]
    nodes.append({**nodes[0], "assetKey": {"path": ["bronze.orders"]}})
    with pytest.raises(ConflictError, match="ambiguous"):
        preflight("bronze.orders")
    assert preflight("bronze/orders") == dotted


def test_incident_preflight_uses_staging_repository_graph_and_rejects_local_ambiguity(api):
    _, _, monkeypatch = api
    from phlo_api.api import operation_controls, v1_assets
    from phlo_api.errors import BackendUnavailableError

    for gate in (
        "PHLO_V1_ACTIONS_SINGLE_REPLICA",
        "PHLO_V1_ACTIONS_SINGLE_PROCESS",
        "PHLO_V1_ACTIONS_REF_TAG_CONTRACT",
    ):
        monkeypatch.setenv(gate, "1")
    monkeypatch.setattr(
        operation_controls, "require_scope", lambda request, scope: {"subject": "operator"}
    )
    nodes = [
        {
            "assetKey": {"path": [layer, "orders"]},
            "dependencyKeys": [{"path": ["bronze", "orders"]}] if layer == "gold" else [],
            "metadataEntries": [{"label": "phlo/layer", "text": layer}],
            "jobNames": ["orders"] if layer == "gold" else ["ingest"],
            "repository": {"name": "repo", "location": {"name": location}},
        }
        for location in ("prod_loc", "stage_loc")
        for layer in ("bronze", "gold")
    ]

    async def asset_graph(url, query, variables=None):
        return _repository_asset_response(nodes)

    monkeypatch.setattr(v1_assets, "graphql_request", asset_graph)

    def preflight(env):
        request = Request({"type": "http", "headers": [], "query_string": f"env={env}".encode()})
        return asyncio.run(
            v1_jobs.preflight_incident_pause(request, env=env, asset_ids=["bronze.orders"])
        )

    targets = preflight("staging")
    assert len(targets) == 1
    assert (
        targets[0]["env"],
        targets[0]["location"],
        targets[0]["ref"],
        targets[0]["repository_name"],
    ) == (
        "staging",
        "stage_loc",
        "candidate",
        "repo",
    )
    assert targets[0]["asset_ids"] == ["gold/orders"]
    assert targets[0]["affected_asset_ids"] == ["bronze/orders"]
    nodes.append({**nodes[-1], "repository": {"name": "other", "location": {"name": "stage_loc"}}})
    with pytest.raises(BackendUnavailableError, match="ambiguous"):
        preflight("staging")
    assert preflight("prod")[0]["location"] == "prod_loc"


def test_operational_windows_merge_without_overriding_operator_policy(api):
    client, _, monkeypatch = api
    from phlo.plugins.observatory_settings import StorageUnavailableError
    from phlo_api.observatory_api import settings

    operator = {
        "id": "operator",
        "starts_at": "2026-10-02T01:00:00Z",
        "ends_at": "2026-10-02T02:00:00Z",
        "description": "Operator window",
    }
    computed = {
        **operator,
        "id": "policy-slot",
        "description": "Policy evaluation, not observed downtime",
    }
    monkeypatch.setenv(
        "PHLO_V1_MAINTENANCE_WINDOWS", json.dumps({"prod": [operator], "staging": []})
    )
    calls = []

    def windows(env):
        calls.append(env)
        return [{**operator, "description": "Do not replace"}, computed] if env == "prod" else []

    monkeypatch.setattr(settings, "get_operational_maintenance_windows", windows)
    result = client.get("/api/v1/maintenance-windows?env=prod").json()
    assert [item["id"] for item in result["items"]] == ["operator", "policy-slot"]
    assert result["items"][0]["description"] == "Operator window"
    assert calls == ["prod"]
    monkeypatch.delenv("PHLO_V1_MAINTENANCE_WINDOWS")
    assert client.get("/api/v1/maintenance-windows?env=staging").json()["status"] == "configured"

    def unavailable(env):
        raise StorageUnavailableError("unavailable")

    monkeypatch.setattr(settings, "get_operational_maintenance_windows", unavailable)
    assert client.get("/api/v1/maintenance-windows?env=prod").status_code == 503
