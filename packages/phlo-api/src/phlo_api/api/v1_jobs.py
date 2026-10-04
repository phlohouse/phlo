"""Bounded, environment-scoped job and run reads for ``/api/v1``."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from statistics import median
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import AwareDatetime, Field

from phlo_api.api.v1 import _target
from phlo_api.errors import BackendUnavailableError, BadGatewayError, ConflictError, NotFoundError
from phlo_api.observatory_api.dagster import graphql_request, resolve_dagster_url
from phlo_api.v1_contract import Environment, RunStatus, WireModel

router = APIRouter(tags=["v1 jobs and runs"])
Limit = Annotated[int, Query(ge=1, le=100)]
_INVENTORY_LIMIT = 500
_PATTERN_SCAN_LIMIT = 200
_V1_ACTION_LOCK = asyncio.Lock()

JOBS_QUERY = """query V1Jobs {
  repositoriesOrError {
    __typename
    ... on RepositoryConnection {
      nodes {
        name location { name }
        pipelines { name description }
        schedules { id name pipelineName scheduleState { id status } }
      }
    }
  }
}"""
ASSET_JOBS_QUERY = """query V1AssetJobs {
  assetNodes {
    assetKey { path }
    jobNames
    repository { location { name } }
  }
}"""
LAUNCH_JOB_MUTATION = """mutation V1LaunchJob($executionParams: ExecutionParams!) {
  launchPipelineExecution(executionParams: $executionParams) {
    __typename
    ... on LaunchRunSuccess { run { runId status } }
    ... on PipelineNotFoundError { message }
    ... on InvalidSubsetError { message }
    ... on RunConfigValidationInvalid { errors { message } }
    ... on PythonError { message }
  }
}"""
SCHEDULE_START_MUTATION = """mutation V1ScheduleStart($scheduleSelector: ScheduleSelector!) {
  startSchedule(scheduleSelector: $scheduleSelector) {
    __typename
    ... on ScheduleStateResult { scheduleState { status } }
    ... on ScheduleNotFoundError { message }
    ... on UnauthorizedError { message }
    ... on PythonError { message }
  }
}"""
SCHEDULE_STOP_MUTATION = """mutation V1ScheduleStop($scheduleId: String!) {
  stopRunningSchedule(id: $scheduleId) {
    __typename
    ... on ScheduleStateResult { scheduleState { status } }
    ... on ScheduleNotFoundError { message }
    ... on UnauthorizedError { message }
    ... on PythonError { message }
  }
}"""
RUNS_QUERY = """query V1Runs($limit: Int!) {
  runsOrError(limit: $limit) {
    __typename
    ... on Runs {
      results {
        runId status creationTime startTime endTime pipelineName
        assetSelection { path }
        tags { key value }
        repositoryOrigin { repositoryLocationName }
      }
    }
  }
}"""
RUN_QUERY = """query V1Run($runId: ID!, $eventLimit: Int!, $afterCursor: String) {
  runOrError(runId: $runId) {
    __typename
    ... on Run {
      runId status creationTime startTime endTime pipelineName
      assetSelection { path }
      tags { key value }
      repositoryOrigin { repositoryLocationName }
      eventConnection(limit: $eventLimit, afterCursor: $afterCursor) {
        events {
          __typename
          ... on MessageEvent { eventType message timestamp stepKey }
        }
        cursor
        hasMore
      }
    }
    ... on RunNotFoundError { message }
  }
}"""


class Job(WireModel):
    id: str
    repository_name: str
    description: str | None
    selected_assets: list[list[str]]
    assets_url: str
    runs_url: str
    incidents_url: str
    resource_id: str


class JobPage(WireModel):
    env: Environment
    items: list[Job]
    next_cursor: None = None


class Schedule(WireModel):
    id: str
    job_id: str
    status: str
    resource_id: str


class SchedulePage(WireModel):
    env: Environment
    items: list[Schedule]


class Run(WireModel):
    run_id: str
    job_id: str
    status: RunStatus
    created_at: AwareDatetime
    started_at: AwareDatetime | None
    ended_at: AwareDatetime | None
    duration_seconds: float | None = Field(ge=0)
    selected_assets: list[list[str]]
    logs_url: str
    resource_id: str


class RunPage(WireModel):
    env: Environment
    items: list[Run]
    next_cursor: None = None


class RunEvent(WireModel):
    event_type: str
    message: str
    timestamp: AwareDatetime
    step_key: str | None


class RunTimeline(WireModel):
    env: Environment
    run_id: str
    items: list[RunEvent]
    truncated: bool
    next_cursor: str | None


class RunLogs(RunTimeline):
    follow_supported: bool = True
    status: RunStatus
    is_terminal: bool


class RunPattern(WireModel):
    kind: Literal["failure", "slow_run"]
    job_id: str
    count: int = Field(ge=1)
    run_ids: list[str]
    typical_duration_seconds: float | None = Field(default=None, ge=0)


class PatternPage(WireModel):
    env: Environment
    job_id: str
    items: list[RunPattern]
    scanned_runs: int = Field(ge=0, le=_PATTERN_SCAN_LIMIT)


class JobSummary(WireModel):
    env: Environment
    job_id: str
    scanned_runs: int = Field(ge=0, le=_PATTERN_SCAN_LIMIT)
    counts_by_status: dict[str, int]
    duration_histogram_seconds: dict[str, int]


class MaintenanceWindow(WireModel):
    id: str
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    description: str | None


class MaintenanceWindowPage(WireModel):
    env: Environment
    status: Literal["configured", "unavailable"]
    items: list[MaintenanceWindow]


class JobLaunchRequest(WireModel):
    idempotency_key: str = Field(min_length=1, max_length=256)
    dry_run: bool = True
    confirmed: bool = False
    run_config: dict[str, Any] = Field(default_factory=dict)


class ScheduleActionRequest(WireModel):
    idempotency_key: str = Field(min_length=1, max_length=256)
    expected_status: Literal["RUNNING", "STOPPED"]
    confirmed: bool = False


class RunActionRequest(WireModel):
    idempotency_key: str = Field(min_length=1, max_length=256)
    expected_status: RunStatus
    dry_run: bool = True
    confirmed: bool = False
    reason: str | None = Field(default=None, max_length=1000)


class ActionResult(WireModel):
    env: Environment
    action: str
    target_id: str
    status: Literal["accepted", "skipped", "rejected"]
    result: dict[str, Any]


async def _graphql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        result = await graphql_request(resolve_dagster_url(), query, variables)
    except (httpx.HTTPError, OSError, RuntimeError) as exc:
        raise BackendUnavailableError("Dagster is unavailable.") from exc
    except ValueError as exc:
        raise BadGatewayError("Dagster returned invalid JSON.") from exc
    if not isinstance(result, dict) or result.get("errors"):
        raise BadGatewayError("Dagster returned an invalid response.")
    return result


def _field(result: dict[str, Any], name: str, typename: str) -> dict[str, Any]:
    data = result.get("data")
    value = data.get(name) if isinstance(data, dict) else None
    if not isinstance(value, dict) or value.get("__typename") != typename:
        raise BadGatewayError("Dagster returned an invalid response.")
    return value


def _mutation_field(
    result: dict[str, Any], name: str, success_type: str, *, target_type: str
) -> dict[str, Any]:
    data = result.get("data")
    value = data.get(name) if isinstance(data, dict) else None
    if not isinstance(value, dict) or not isinstance(value.get("__typename"), str):
        raise BadGatewayError("Dagster returned an invalid mutation response.")
    typename = value["__typename"]
    if typename == success_type:
        return value
    if typename.endswith("NotFoundError"):
        raise NotFoundError(f"{target_type} was not found in the selected environment.")
    if typename == "UnauthorizedError":
        raise HTTPException(status_code=403, detail="Dagster denied the requested action.")
    if typename == "PythonError":
        raise BackendUnavailableError("Dagster could not complete the requested action.")
    raise HTTPException(status_code=422, detail="Dagster rejected the requested action.")


def _time(value: Any, *, required: bool = False) -> datetime | None:
    if value is None and not required:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise BadGatewayError("Dagster returned an invalid timestamp.")
    return datetime.fromtimestamp(value, UTC)


def _identity(row: dict[str, Any], location: str, ref: str) -> bool:
    origin = row.get("repositoryOrigin")
    if not isinstance(origin, dict) or not isinstance(origin.get("repositoryLocationName"), str):
        raise BadGatewayError("Dagster run has no location identity.")
    if origin["repositoryLocationName"] != location:
        return False
    tags = row.get("tags")
    if not isinstance(tags, list) or any(
        not isinstance(tag, dict)
        or not isinstance(tag.get("key"), str)
        or not isinstance(tag.get("value"), str)
        for tag in tags
    ):
        raise BadGatewayError("Dagster returned invalid run tags.")
    refs = [tag["value"] for tag in tags if tag["key"] == "phlo/ref"]
    if len(refs) != 1:
        raise BackendUnavailableError("Dagster run has no verified ref identity.")
    return refs[0] == ref


def _assets(value: Any) -> list[list[str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise BadGatewayError("Dagster returned an invalid asset selection.")
    result: list[list[str]] = []
    for item in value:
        path = item.get("path") if isinstance(item, dict) else None
        if path is None and isinstance(item, dict) and isinstance(item.get("assetKey"), dict):
            path = item["assetKey"].get("path")
        if (
            not isinstance(path, list)
            or not path
            or not all(isinstance(part, str) for part in path)
        ):
            raise BadGatewayError("Dagster returned an invalid asset selection.")
        result.append(path)
    return result


def _run(row: Any, location: str, ref: str) -> Run | None:
    if not isinstance(row, dict):
        raise BadGatewayError("Dagster returned an invalid run.")
    if not _identity(row, location, ref):
        return None
    run_id, job_id, status = row.get("runId"), row.get("pipelineName"), row.get("status")
    if not isinstance(run_id, str) or not run_id or not isinstance(job_id, str) or not job_id:
        raise BadGatewayError("Dagster returned an invalid run.")
    if status not in RunStatus.__args__:
        raise BadGatewayError("Dagster returned an invalid run status.")
    created = _time(row.get("creationTime"), required=True)
    started, ended = _time(row.get("startTime")), _time(row.get("endTime"))
    duration = None
    if started is not None and ended is not None:
        duration = (ended - started).total_seconds()
        if duration < 0:
            raise BadGatewayError("Dagster returned an invalid run duration.")
    assert created is not None
    return Run(
        run_id=run_id,
        job_id=job_id,
        status=status,
        created_at=created,
        started_at=started,
        ended_at=ended,
        duration_seconds=duration,
        selected_assets=_assets(row.get("assetSelection")),
        logs_url=f"/api/v1/runs/{run_id}/logs",
        resource_id=f"dagster-run:{location}:{run_id}",
    )


async def _scoped_runs(location: str, ref: str, limit: int) -> list[Run]:
    value = _field(await _graphql(RUNS_QUERY, {"limit": limit}), "runsOrError", "Runs")
    rows = value.get("results")
    if not isinstance(rows, list) or len(rows) > limit:
        raise BadGatewayError("Dagster returned an invalid run list.")
    return [run for row in rows if (run := _run(row, location, ref)) is not None]


def _repositories(result: dict[str, Any], location: str) -> list[dict[str, Any]]:
    value = _field(result, "repositoriesOrError", "RepositoryConnection")
    nodes = value.get("nodes")
    if not isinstance(nodes, list) or len(nodes) > _INVENTORY_LIMIT:
        raise BadGatewayError("Dagster returned an invalid job inventory.")
    repositories: list[dict[str, Any]] = []
    for node in nodes:
        node_location = node.get("location") if isinstance(node, dict) else None
        if not isinstance(node_location, dict) or not isinstance(node_location.get("name"), str):
            raise BadGatewayError("Dagster returned an invalid repository.")
        if node_location["name"] == location:
            repositories.append(node)
    return repositories


async def _job_data(
    request: Request, env: Environment, allowed: frozenset[str]
) -> tuple[Any, list[dict[str, Any]], dict[str, list[list[str]]]]:
    target = _target(request, env, allowed_query=allowed)
    repositories = _repositories(await _graphql(JOBS_QUERY), target.dagster_location)
    result = await _graphql(ASSET_JOBS_QUERY)
    data = result.get("data")
    nodes = data.get("assetNodes") if isinstance(data, dict) else None
    if not isinstance(nodes, list) or len(nodes) > _INVENTORY_LIMIT:
        raise BadGatewayError("Dagster returned an invalid asset-job inventory.")
    assets: dict[str, list[list[str]]] = {}
    for node in nodes:
        repository = node.get("repository") if isinstance(node, dict) else None
        location = repository.get("location") if isinstance(repository, dict) else None
        job_names = node.get("jobNames") if isinstance(node, dict) else None
        if (
            not isinstance(location, dict)
            or not isinstance(location.get("name"), str)
            or not isinstance(job_names, list)
            or any(not isinstance(name, str) for name in job_names)
        ):
            raise BadGatewayError("Dagster returned an invalid asset-job record.")
        if location["name"] != target.dagster_location:
            continue
        path = _assets([node.get("assetKey")])[0]
        for name in job_names:
            assets.setdefault(name, []).append(path)
    return target, repositories, assets


def _jobs(
    repositories: list[dict[str, Any]], env: Environment, assets: dict[str, list[list[str]]]
) -> list[Job]:
    items: list[Job] = []
    for repository in repositories:
        name, pipelines = repository.get("name"), repository.get("pipelines")
        if not isinstance(name, str) or not isinstance(pipelines, list):
            raise BadGatewayError("Dagster returned an invalid job inventory.")
        for pipeline in pipelines:
            if not isinstance(pipeline, dict) or not isinstance(pipeline.get("name"), str):
                raise BadGatewayError("Dagster returned an invalid job.")
            job_id = pipeline["name"]
            items.append(
                Job(
                    id=job_id,
                    repository_name=name,
                    description=pipeline.get("description")
                    if isinstance(pipeline.get("description"), str)
                    else None,
                    selected_assets=assets.get(job_id, []),
                    assets_url=f"/api/v1/assets?env={env}",
                    runs_url=f"/api/v1/runs?env={env}&job_id={job_id}",
                    incidents_url=f"/api/v1/incidents?env={env}",
                    resource_id=f"dagster-job:{repository['location']['name']}:{name}:{job_id}",
                )
            )
    if len(items) > _INVENTORY_LIMIT:
        raise BadGatewayError("Dagster job inventory exceeds the v1 limit.")
    return sorted(items, key=lambda item: (item.repository_name, item.id))


@router.get("/jobs", response_model=JobPage)
async def v1_jobs(request: Request, env: Environment = Query()) -> JobPage:
    _, repositories, assets = await _job_data(request, env, frozenset({"env"}))
    return JobPage(env=env, items=_jobs(repositories, env, assets))


@router.get("/jobs/{job_id}", response_model=Job)
async def v1_job(request: Request, job_id: str, env: Environment = Query()) -> Job:
    _, repositories, assets = await _job_data(request, env, frozenset({"env"}))
    matches = [item for item in _jobs(repositories, env, assets) if item.id == job_id]
    if len(matches) != 1:
        raise NotFoundError("Job was not found.")
    return matches[0]


@router.get("/jobs/{job_id}/schedules", response_model=SchedulePage)
async def v1_job_schedules(
    request: Request, job_id: str, env: Environment = Query()
) -> SchedulePage:
    _, repositories, assets = await _job_data(request, env, frozenset({"env"}))
    if not any(item.id == job_id for item in _jobs(repositories, env, assets)):
        raise NotFoundError("Job was not found.")
    items: list[Schedule] = []
    for repository in repositories:
        schedules = repository.get("schedules")
        if not isinstance(schedules, list):
            raise BadGatewayError("Dagster returned an invalid schedule inventory.")
        for schedule in schedules:
            state = schedule.get("scheduleState") if isinstance(schedule, dict) else None
            if (
                not isinstance(state, dict)
                or not all(isinstance(schedule.get(key), str) for key in ("name", "pipelineName"))
                or not isinstance(state.get("status"), str)
            ):
                raise BadGatewayError("Dagster returned an invalid schedule.")
            if schedule["pipelineName"] == job_id:
                items.append(
                    Schedule(
                        id=schedule["name"],
                        job_id=job_id,
                        status=state["status"],
                        resource_id=f"dagster-schedule:{repository['location']['name']}:{repository['name']}:{schedule['name']}",
                    )
                )
    return SchedulePage(env=env, items=sorted(items, key=lambda item: item.id))


@router.get("/runs", response_model=RunPage)
async def v1_runs(
    request: Request, env: Environment = Query(), limit: Limit = 100, job_id: str | None = None
) -> RunPage:
    target = _target(request, env, allowed_query=frozenset({"env", "limit", "job_id"}))
    items = await _scoped_runs(target.dagster_location, target.nessie_ref, limit)
    if job_id is not None:
        items = [item for item in items if item.job_id == job_id]
    return RunPage(env=env, items=items)


async def _run_detail(
    request: Request,
    env: Environment,
    run_id: str,
    event_limit: int,
    after_cursor: str | None = None,
) -> tuple[Run, dict[str, Any]]:
    target = _target(
        request,
        env,
        allowed_query=frozenset({"env", "limit", "after_cursor"}),
    )
    result = await _graphql(
        RUN_QUERY,
        {"runId": run_id, "eventLimit": event_limit, "afterCursor": after_cursor},
    )
    data = result.get("data")
    value = data.get("runOrError") if isinstance(data, dict) else None
    if isinstance(value, dict) and value.get("__typename") == "RunNotFoundError":
        raise NotFoundError("Run was not found.")
    if not isinstance(value, dict) or value.get("__typename") != "Run":
        raise BadGatewayError("Dagster returned an invalid run response.")
    run = _run(value, target.dagster_location, target.nessie_ref)
    if run is None:
        raise NotFoundError("Run was not found.")
    return run, value


@router.get("/runs/{run_id}", response_model=Run)
async def v1_run(request: Request, run_id: str, env: Environment = Query()) -> Run:
    run, _ = await _run_detail(request, env, run_id, 1)
    return run


def _events(value: dict[str, Any], limit: int) -> tuple[list[RunEvent], bool, str]:
    connection = value.get("eventConnection")
    rows = connection.get("events") if isinstance(connection, dict) else None
    cursor = connection.get("cursor") if isinstance(connection, dict) else None
    has_more = connection.get("hasMore") if isinstance(connection, dict) else None
    if (
        not isinstance(rows, list)
        or len(rows) > limit
        or not isinstance(cursor, str)
        or not isinstance(has_more, bool)
    ):
        raise BadGatewayError("Dagster returned invalid run events.")
    events: list[RunEvent] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            raise BadGatewayError("Dagster returned an invalid run event.")
        event_type = row.get("eventType") or row.get("__typename")
        if not isinstance(event_type, str) or not isinstance(row.get("message"), str):
            raise BadGatewayError("Dagster returned an invalid run event.")
        timestamp = row.get("timestamp")
        try:
            observed = datetime.fromtimestamp(float(timestamp) / 1000, UTC)
        except (TypeError, ValueError, OSError) as exc:
            raise BadGatewayError("Dagster returned an invalid event timestamp.") from exc
        step_key = row.get("stepKey")
        if step_key is not None and not isinstance(step_key, str):
            raise BadGatewayError("Dagster returned an invalid run event.")
        events.append(
            RunEvent(
                event_type=event_type, message=row["message"], timestamp=observed, step_key=step_key
            )
        )
    return events, has_more, cursor


@router.get("/runs/{run_id}/timeline", response_model=RunTimeline)
async def v1_run_timeline(
    request: Request,
    run_id: str,
    env: Environment = Query(),
    limit: Limit = 100,
    after_cursor: Annotated[str | None, Query(max_length=4096)] = None,
) -> RunTimeline:
    run, value = await _run_detail(request, env, run_id, limit, after_cursor)
    items, truncated, cursor = _events(value, limit)
    return RunTimeline(
        env=env,
        run_id=run.run_id,
        items=items,
        truncated=truncated,
        next_cursor=cursor or None,
    )


@router.get("/runs/{run_id}/logs", response_model=RunLogs)
async def v1_run_logs(
    request: Request,
    run_id: str,
    env: Environment = Query(),
    limit: Limit = 100,
    after_cursor: Annotated[str | None, Query(max_length=4096)] = None,
) -> RunLogs:
    run, value = await _run_detail(request, env, run_id, limit, after_cursor)
    items, truncated, cursor = _events(value, limit)
    return RunLogs(
        env=env,
        run_id=run.run_id,
        items=items,
        truncated=truncated,
        next_cursor=cursor or None,
        status=run.status,
        is_terminal=run.status in {"SUCCESS", "FAILURE", "CANCELED"},
    )


@router.get("/jobs/{job_id}/patterns", response_model=PatternPage)
async def v1_job_patterns(request: Request, job_id: str, env: Environment = Query()) -> PatternPage:
    target = _target(request, env, allowed_query=frozenset({"env"}))
    _, repositories, assets = await _job_data(request, env, frozenset({"env"}))
    if not any(item.id == job_id for item in _jobs(repositories, env, assets)):
        raise NotFoundError("Job was not found.")
    runs = [
        run
        for run in await _scoped_runs(
            target.dagster_location, target.nessie_ref, _PATTERN_SCAN_LIMIT
        )
        if run.job_id == job_id
    ]
    items: list[RunPattern] = []
    failed = [run.run_id for run in runs if run.status == "FAILURE"]
    if failed:
        items.append(RunPattern(kind="failure", job_id=job_id, count=len(failed), run_ids=failed))
    durations = [
        run.duration_seconds
        for run in runs
        if run.status == "SUCCESS" and run.duration_seconds is not None
    ]
    if durations:
        typical = median(durations)
        slow = [
            run.run_id
            for run in runs
            if run.status == "SUCCESS"
            and run.duration_seconds is not None
            and run.duration_seconds > max(60, typical * 2)
        ]
        if slow:
            items.append(
                RunPattern(
                    kind="slow_run",
                    job_id=job_id,
                    count=len(slow),
                    run_ids=slow,
                    typical_duration_seconds=typical,
                )
            )
    return PatternPage(env=env, job_id=job_id, items=items, scanned_runs=len(runs))


@router.get("/jobs/{job_id}/summary", response_model=JobSummary)
async def v1_job_summary(request: Request, job_id: str, env: Environment = Query()) -> JobSummary:
    target = _target(request, env, allowed_query=frozenset({"env"}))
    _, repositories, assets = await _job_data(request, env, frozenset({"env"}))
    if not any(item.id == job_id for item in _jobs(repositories, env, assets)):
        raise NotFoundError("Job was not found.")
    runs = [
        run
        for run in await _scoped_runs(
            target.dagster_location, target.nessie_ref, _PATTERN_SCAN_LIMIT
        )
        if run.job_id == job_id
    ]
    counts: dict[str, int] = {}
    buckets = {"<60": 0, "60-299": 0, "300-899": 0, ">=900": 0}
    for run in runs:
        counts[run.status] = counts.get(run.status, 0) + 1
        if run.duration_seconds is not None:
            bucket = (
                "<60"
                if run.duration_seconds < 60
                else "60-299"
                if run.duration_seconds < 300
                else "300-899"
                if run.duration_seconds < 900
                else ">=900"
            )
            buckets[bucket] += 1
    return JobSummary(
        env=env,
        job_id=job_id,
        scanned_runs=len(runs),
        counts_by_status=counts,
        duration_histogram_seconds=buckets,
    )


@router.get("/schedules", response_model=SchedulePage)
async def v1_schedules(request: Request, env: Environment = Query()) -> SchedulePage:
    _, repositories, _ = await _job_data(request, env, frozenset({"env"}))
    items: list[Schedule] = []
    for repository in repositories:
        schedules = repository.get("schedules")
        if not isinstance(schedules, list):
            raise BadGatewayError("Dagster returned an invalid schedule inventory.")
        for schedule in schedules:
            state = schedule.get("scheduleState") if isinstance(schedule, dict) else None
            if (
                not isinstance(state, dict)
                or not all(isinstance(schedule.get(key), str) for key in ("name", "pipelineName"))
                or not isinstance(state.get("status"), str)
            ):
                raise BadGatewayError("Dagster returned an invalid schedule.")
            items.append(
                Schedule(
                    id=schedule["name"],
                    job_id=schedule["pipelineName"],
                    status=state["status"],
                    resource_id=f"dagster-schedule:{repository['location']['name']}:{repository['name']}:{schedule['name']}",
                )
            )
    return SchedulePage(env=env, items=sorted(items, key=lambda item: (item.job_id, item.id)))


@router.get("/maintenance-windows", response_model=MaintenanceWindowPage)
async def v1_maintenance_windows(
    request: Request, env: Environment = Query()
) -> MaintenanceWindowPage:
    _target(request, env, allowed_query=frozenset({"env"}))
    raw = os.environ.get("PHLO_V1_MAINTENANCE_WINDOWS")
    if not raw:
        return MaintenanceWindowPage(env=env, status="unavailable", items=[])
    try:
        if len(raw) > 65_536:
            raise ValueError
        payload = json.loads(raw)
        windows = payload[env]
        if not isinstance(windows, list) or len(windows) > 100:
            raise ValueError
        items = [MaintenanceWindow.model_validate_json(json.dumps(item)) for item in windows]
        if any(item.ends_at <= item.starts_at for item in items) or len(
            {item.id for item in items}
        ) != len(items):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise BackendUnavailableError("Configured maintenance windows are unavailable.") from exc
    return MaintenanceWindowPage(env=env, status="configured", items=items)


def _require_single_replica_actions() -> None:
    if any(
        os.environ.get(name) != "1"
        for name in (
            "PHLO_V1_ACTIONS_SINGLE_REPLICA",
            "PHLO_V1_ACTIONS_SINGLE_PROCESS",
            "PHLO_V1_ACTIONS_REF_TAG_CONTRACT",
        )
    ):
        raise BackendUnavailableError("Environment-pinned job actions are not enabled.")


async def _serialize_action(execute: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    async with _V1_ACTION_LOCK:
        return await execute()


def _action_helpers(request: Request, operation: str) -> tuple[dict[str, Any], Any, Any, Any, Any]:
    from phlo_api.api.operation_controls import (
        audit_operation,
        enforce_rate_limit,
        idempotency_key_target,
        replay_or_execute_async,
        require_scope,
    )
    from phlo_api.observatory_api.run_action_contract import require_idempotency_key

    auth = require_scope(request, "lakehouse:operate")
    enforce_rate_limit(auth["subject"], operation)
    return (
        auth,
        audit_operation,
        idempotency_key_target,
        replay_or_execute_async,
        require_idempotency_key,
    )


def _digest(payload: WireModel) -> str:
    body = payload.model_dump(exclude={"idempotency_key"}, mode="json")
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _repository(repositories: list[dict[str, Any]], repository_name: str) -> dict[str, Any]:
    matches = [item for item in repositories if item.get("name") == repository_name]
    if len(matches) != 1:
        raise NotFoundError("Repository was not found in the selected environment.")
    return matches[0]


@router.post("/jobs/{job_id}/launch", response_model=ActionResult)
async def v1_job_launch(
    request: Request,
    job_id: str,
    payload: JobLaunchRequest,
    env: Environment = Query(),
) -> ActionResult:
    auth, audit, key_target, replay, require_key = _action_helpers(request, "launch_job")
    _require_single_replica_actions()
    require_key(payload.idempotency_key)
    if not payload.dry_run and not payload.confirmed:
        raise HTTPException(status_code=422, detail="Live job launch requires confirmation.")
    target = _target(request, env)
    action_target = f"{auth['subject']}:{env}:{target.dagster_location}:{job_id}@{target.nessie_ref}:{_digest(payload)}"
    if key_target(payload.idempotency_key, "v1_job_launch") not in {None, action_target}:
        raise HTTPException(status_code=409, detail={"error": "idempotency_key_conflict"})

    async def execute() -> dict[str, Any]:
        if payload.dry_run:
            _, repositories, assets = await _job_data(request, env, frozenset({"env"}))
            matches = [item for item in _jobs(repositories, env, assets) if item.id == job_id]
            if len(matches) != 1:
                raise NotFoundError("Job was not found in the selected environment.")
            return {"status": "skipped", "message": "Job launch validated; no run was created."}
        _, repositories, assets = await _job_data(request, env, frozenset({"env"}))
        matches = [item for item in _jobs(repositories, env, assets) if item.id == job_id]
        if len(matches) != 1:
            raise NotFoundError("Job was not found in the selected environment.")
        repo = _repository(repositories, matches[0].repository_name)
        result = await _graphql(
            LAUNCH_JOB_MUTATION,
            {
                "executionParams": {
                    "selector": {
                        "pipelineName": job_id,
                        "repositoryLocationName": target.dagster_location,
                        "repositoryName": repo["name"],
                    },
                    "runConfigData": payload.run_config,
                    "mode": "default",
                    "executionMetadata": {
                        "tags": [
                            {"key": "environment", "value": env},
                            {"key": "phlo/ref", "value": target.nessie_ref},
                            {"key": "phlo/operation", "value": "v1_job_launch"},
                            {"key": "phlo/idempotency_key", "value": payload.idempotency_key},
                        ]
                    },
                }
            },
        )
        result_value = _mutation_field(
            result,
            "launchPipelineExecution",
            "LaunchRunSuccess",
            target_type="Job",
        )
        run = result_value.get("run")
        if not isinstance(run, dict) or not isinstance(run.get("runId"), str):
            raise BadGatewayError("Dagster returned an invalid launch result.")
        return {"status": "accepted", "run_id": run["runId"], "run_status": run.get("status")}

    outcome = await replay(
        idempotency_key=payload.idempotency_key,
        operation="v1_job_launch",
        target=action_target,
        execute=lambda: _serialize_action(execute),
        audit=lambda value: audit(
            operation="v1_job_launch",
            target=action_target,
            dry_run=payload.dry_run,
            auth=auth,
            payload={"job_id": job_id, "env": env, "nessie_ref": target.nessie_ref},
            result=value,
        ),
    )
    return ActionResult(
        env=env,
        action="job.launch",
        target_id=job_id,
        status=outcome["status"],
        result=outcome,
    )


@router.post("/schedules/{schedule_id}/{action}", response_model=ActionResult)
async def v1_schedule_action(
    request: Request,
    schedule_id: str,
    action: Literal["pause", "resume"],
    payload: ScheduleActionRequest,
    env: Environment = Query(),
) -> ActionResult:
    auth, audit, key_target, replay, require_key = _action_helpers(request, f"schedule_{action}")
    _require_single_replica_actions()
    require_key(payload.idempotency_key)
    if not payload.confirmed:
        raise HTTPException(status_code=422, detail="Schedule changes require confirmation.")
    target = _target(request, env)
    action_target = f"{auth['subject']}:{env}:{target.dagster_location}:{schedule_id}@{target.nessie_ref}:{_digest(payload)}:{action}"
    if key_target(payload.idempotency_key, "v1_schedule_action") not in {None, action_target}:
        raise HTTPException(status_code=409, detail={"error": "idempotency_key_conflict"})

    async def execute() -> dict[str, Any]:
        _, repositories, _ = await _job_data(request, env, frozenset({"env"}))
        matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for repo in repositories:
            schedules = repo.get("schedules")
            if not isinstance(schedules, list):
                raise BadGatewayError("Dagster returned an invalid schedule inventory.")
            for schedule in schedules:
                if isinstance(schedule, dict) and schedule.get("name") == schedule_id:
                    matches.append((repo, schedule))
        if len(matches) != 1:
            raise NotFoundError("Schedule was not found in the selected environment.")
        repo, schedule = matches[0]
        state = schedule.get("scheduleState")
        current = state.get("status") if isinstance(state, dict) else None
        if current != payload.expected_status:
            raise ConflictError("Schedule state changed; refresh before retrying the action.")
        if (action == "pause" and current == "STOPPED") or (
            action == "resume" and current == "RUNNING"
        ):
            return {"status": "accepted", "schedule_status": current, "changed": False}
        query = SCHEDULE_STOP_MUTATION if action == "pause" else SCHEDULE_START_MUTATION
        if action == "pause":
            result = await _graphql(query, {"scheduleId": schedule.get("id")})
            scheduled = _mutation_field(
                result,
                "stopRunningSchedule",
                "ScheduleStateResult",
                target_type="Schedule",
            )
        else:
            result = await _graphql(
                query,
                {
                    "scheduleSelector": {
                        "repositoryLocationName": target.dagster_location,
                        "repositoryName": repo["name"],
                        "scheduleName": schedule_id,
                    }
                },
            )
            scheduled = _mutation_field(
                result,
                "startSchedule",
                "ScheduleStateResult",
                target_type="Schedule",
            )
        new_state = scheduled.get("scheduleState")
        if not isinstance(new_state, dict) or not isinstance(new_state.get("status"), str):
            raise BadGatewayError("Dagster returned an invalid schedule action result.")
        return {"status": "accepted", "schedule_status": new_state["status"], "changed": True}

    outcome = await replay(
        idempotency_key=payload.idempotency_key,
        operation="v1_schedule_action",
        target=action_target,
        execute=lambda: _serialize_action(execute),
        audit=lambda value: audit(
            operation="v1_schedule_action",
            target=action_target,
            dry_run=False,
            auth=auth,
            payload={"env": env, "action": action, "expected_status": payload.expected_status},
            result=value,
        ),
    )
    return ActionResult(
        env=env,
        action=f"schedule.{action}",
        target_id=schedule_id,
        status="accepted",
        result=outcome,
    )


async def _run_action(
    request: Request,
    run_id: str,
    payload: RunActionRequest,
    env: Environment,
    action: Literal["cancel", "retry"],
) -> ActionResult:
    auth, audit, key_target, replay, require_key = _action_helpers(request, f"run_{action}")
    _require_single_replica_actions()
    require_key(payload.idempotency_key)
    if not payload.dry_run and not payload.confirmed:
        raise HTTPException(status_code=422, detail=f"Live run {action} requires confirmation.")
    target = _target(request, env, allowed_query=frozenset({"env"}))
    action_target = f"{auth['subject']}:{env}:{target.dagster_location}:{run_id}@{target.nessie_ref}:{action}:{_digest(payload)}"
    if key_target(payload.idempotency_key, f"v1_run_{action}") not in {None, action_target}:
        raise HTTPException(status_code=409, detail={"error": "idempotency_key_conflict"})

    async def execute() -> dict[str, Any]:
        run, _ = await _run_detail(request, env, run_id, 1)
        if run.status != payload.expected_status:
            raise ConflictError("Run state changed; refresh before retrying the action.")
        expected = "STARTED" if action == "cancel" else "FAILURE"
        if run.status != expected:
            raise ConflictError(f"Run {action} is not valid for its current status.")
        if payload.dry_run:
            return {"status": "skipped", "message": f"Run {action} validated; no action was sent."}
        from phlo_api.observatory_api.orchestrator_operations import resolve_orchestrator_operations

        provider = resolve_orchestrator_operations()
        if action == "retry":
            result = await provider.retry_run(
                run_id,
                {
                    "dry_run": False,
                    "strategy": "FROM_FAILURE",
                    "idempotency_key": payload.idempotency_key,
                    "tags": {"environment": env, "phlo/ref": target.nessie_ref},
                },
            )
        else:
            result = await provider.cancel_run(
                run_id, {"reason": payload.reason, "idempotency_key": payload.idempotency_key}
            )
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        if hasattr(result, "to_dict"):
            return result.to_dict()
        if isinstance(result, dict):
            return result
        raise BadGatewayError("Dagster returned an invalid run action result.")

    outcome = await replay(
        idempotency_key=payload.idempotency_key,
        operation=f"v1_run_{action}",
        target=action_target,
        execute=lambda: _serialize_action(execute),
        audit=lambda value: audit(
            operation=f"v1_run_{action}",
            target=action_target,
            dry_run=payload.dry_run,
            auth=auth,
            payload={"run_id": run_id, "env": env, "nessie_ref": target.nessie_ref},
            result=value,
        ),
    )
    action_status = (
        "skipped"
        if payload.dry_run
        else "accepted"
        if outcome.get("accepted", True)
        else "rejected"
    )
    return ActionResult(
        env=env,
        action=f"run.{action}",
        target_id=run_id,
        status=action_status,
        result=outcome,
    )


@router.post("/runs/{run_id}/cancel", response_model=ActionResult)
async def v1_run_cancel(
    request: Request, run_id: str, payload: RunActionRequest, env: Environment = Query()
) -> ActionResult:
    return await _run_action(request, run_id, payload, env, "cancel")


@router.post("/runs/{run_id}/retry", response_model=ActionResult)
async def v1_run_retry(
    request: Request, run_id: str, payload: RunActionRequest, env: Environment = Query()
) -> ActionResult:
    return await _run_action(request, run_id, payload, env, "retry")
