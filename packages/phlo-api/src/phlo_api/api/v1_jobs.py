"""Bounded, environment-scoped job and run reads for ``/api/v1``."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from statistics import median
from typing import Annotated, Any, Generic, Literal, TypeVar

import httpx
from anyio.to_thread import run_sync
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import AwareDatetime, Field

from phlo_api.api.v1 import _target
from phlo_api.errors import BackendUnavailableError, BadGatewayError, ConflictError, NotFoundError
from phlo_api.observatory_api.dagster import graphql_request, resolve_dagster_url
from phlo_api.settings import get_deployment_settings, get_process_settings
from phlo_api.v1_contract import Environment, ProviderActionResult, RunStatus, WireModel

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
        pipelines {
          name description tags { key value }
          owners { ... on UserDefinitionOwner { email } ... on TeamDefinitionOwner { team } }
          metadataEntries { label ... on TextMetadataEntry { text } ... on JsonMetadataEntry { jsonString } }
        }
        schedules { id name pipelineName scheduleState { id status } }
      }
    }
  }
}"""
ASSET_JOBS_QUERY = """query V1AssetJobs($pipeline: PipelineSelector) {
  assetNodes(pipeline: $pipeline) {
    assetKey { path }
    jobNames
    groupName
    dependencyKeys { path }
    owners { ... on UserAssetOwner { email } ... on TeamAssetOwner { team } }
    tags { key value }
    metadataEntries { label ... on TextMetadataEntry { text } ... on JsonMetadataEntry { jsonString } }
    repository { name location { name } }
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
RUNS_QUERY = """query V1Runs($limit: Int!, $cursor: String, $filter: RunsFilter) {
  runsOrError(limit: $limit, cursor: $cursor, filter: $filter) {
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
          ... on MessageEvent { eventType message timestamp stepKey level }
          ... on ExecutionStepFailureEvent {
            error { message className stack causes { message className stack causes { message className stack } } }
          }
          ... on RunFailureEvent {
            error { message className stack causes { message className stack causes { message className stack } } }
          }
          ... on LogsCapturedEvent { fileKey stepKeys }
        }
        cursor
        hasMore
      }
    }
    ... on RunNotFoundError { message }
  }
}"""
RUN_CAPTURED_LOGS_QUERY = """query V1RunCapturedLogs($runId: ID!, $fileKey: String!) {
  runOrError(runId: $runId) {
    __typename
    ... on Run {
      ... on PipelineRun {
        capturedLogs(fileKey: $fileKey) { stdout stderr }
      }
    }
    ... on RunNotFoundError { message }
  }
}"""
_CAPTURED_LOG_BYTES = 256 * 1024


class Job(WireModel):
    id: str
    repository_name: str
    description: str | None
    domain: str | None = None
    owners: list[str] = Field(default_factory=list)
    source: str | None = None
    feeds_batch_release: bool = False
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
    tags: dict[str, str] = Field(default_factory=dict)
    logs_url: str
    resource_id: str


class RunPage(WireModel):
    env: Environment
    items: list[Run]
    next_cursor: str | None = None


class RunEvent(WireModel):
    event_type: str
    message: str
    timestamp: AwareDatetime
    step_key: str | None
    level: str | None = None
    error: RunError | None = None
    captured_file_key: str | None = None
    captured_step_keys: list[str] = Field(default_factory=list)


class RunError(WireModel):
    message: str
    class_name: str | None = None
    stack: list[str] = Field(default_factory=list)
    causes: list[RunError] = Field(default_factory=list)


RunError.model_rebuild()
RunEvent.model_rebuild()


class CapturedRunLogs(WireModel):
    file_key: str
    step_keys: list[str]
    stdout: str | None
    stderr: str | None
    available: bool
    truncated: bool = False


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
    captured_logs: list[CapturedRunLogs] = Field(default_factory=list)
    captured_logs_available: bool = True


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
    partition_key: str | None = Field(default=None, min_length=1, max_length=256)


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


class SkippedActionResult(WireModel):
    status: Literal["skipped"]
    message: str


class JobLaunchResult(WireModel):
    status: Literal["accepted"]
    run_id: str
    run_status: RunStatus | None


class ScheduleActionResult(WireModel):
    status: Literal["accepted"]
    schedule_status: Literal["RUNNING", "STOPPED"]
    changed: bool


Outcome = TypeVar("Outcome")


class ActionResult(WireModel, Generic[Outcome]):
    env: Environment
    action: str
    target_id: str
    status: Literal["accepted", "skipped", "rejected"]
    result: Outcome


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
        tags={
            tag["key"]: tag["value"]
            for tag in row["tags"]
            if tag["key"] in {"dagster/schedule_name", "dagster/sensor_name", "phlo/operation"}
        },
        logs_url=f"/api/v1/runs/{run_id}/logs",
        resource_id=f"dagster-run:{location}:{run_id}",
    )


async def _run_page(
    location: str,
    ref: str,
    limit: int,
    cursor: str | None = None,
    job_id: str | None = None,
    status: RunStatus | None = None,
) -> tuple[list[Run], str | None]:
    filters: dict[str, Any] = {"tags": [{"key": "phlo/ref", "value": ref}]}
    if job_id is not None:
        filters["pipelineName"] = job_id
    if status is not None:
        filters["statuses"] = [status]
    value = _field(
        await _graphql(RUNS_QUERY, {"limit": limit, "cursor": cursor, "filter": filters}),
        "runsOrError",
        "Runs",
    )
    rows = value.get("results")
    if not isinstance(rows, list) or len(rows) > limit:
        raise BadGatewayError("Dagster returned an invalid run list.")
    items = [run for row in rows if (run := _run(row, location, ref)) is not None]
    items = [
        run
        for run in items
        if (job_id is None or run.job_id == job_id) and (status is None or run.status == status)
    ]
    next_cursor = rows[-1]["runId"] if len(rows) == limit else None
    if next_cursor == cursor and next_cursor is not None:
        raise BadGatewayError("Dagster run cursor did not advance.")
    return items, next_cursor


async def _scoped_runs(location: str, ref: str, limit: int, job_id: str | None = None) -> list[Run]:
    items: list[Run] = []
    cursor = None
    while len(items) < limit:
        page, cursor = await _run_page(location, ref, limit - len(items), cursor, job_id)
        items.extend(page)
        if cursor is None:
            break
    return items


def _definition_metadata(node: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for entry in node.get("metadataEntries", []):
        if isinstance(entry, dict) and isinstance(entry.get("label"), str):
            text = entry.get("text", entry.get("jsonString"))
            if isinstance(text, str) and text:
                values[entry["label"]] = text
    for tag in node.get("tags", []):
        if (
            isinstance(tag, dict)
            and isinstance(tag.get("key"), str)
            and isinstance(tag.get("value"), str)
        ):
            values[tag["key"]] = tag["value"]
    if isinstance(node.get("groupName"), str):
        values.setdefault("group", node["groupName"])
    if "source_name" in values:
        values["source"] = values["source_name"]
    return values


def _definition_owners(node: dict[str, Any]) -> list[str]:
    owners = [
        owner.get("email") or owner.get("team")
        for owner in node.get("owners", [])
        if isinstance(owner, dict)
    ]
    declared = _definition_metadata(node).get("owner")
    if declared:
        owners.append(declared)
    return sorted({owner for owner in owners if isinstance(owner, str) and owner})


def _feeds_release(metadata: dict[str, str]) -> bool:
    try:
        consumers = json.loads(metadata.get("consumers", "[]"))
    except ValueError as exc:
        raise BadGatewayError("Dagster returned invalid consumer metadata.") from exc
    if not isinstance(consumers, list):
        raise BadGatewayError("Dagster returned invalid consumer metadata.")
    return any(
        (item.get("name") if isinstance(item, dict) else item)
        in {"batch_release", "Batch release", "batch release"}
        for item in consumers
        if isinstance(item, (str, dict))
    )


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
) -> tuple[Any, list[dict[str, Any]], dict[tuple[str, str], list[list[str]]]]:
    target = _target(request, env, allowed_query=allowed)
    repositories = _repositories(await _graphql(JOBS_QUERY), target.dagster_location)
    assets: dict[tuple[str, str], list[list[str]]] = {}
    for repository in repositories:
        pipelines = repository.get("pipelines")
        if not isinstance(repository.get("name"), str) or not isinstance(pipelines, list):
            raise BadGatewayError("Dagster returned an invalid job inventory.")
        for pipeline in pipelines:
            if not isinstance(pipeline, dict) or not isinstance(pipeline.get("name"), str):
                raise BadGatewayError("Dagster returned an invalid job.")
            nodes = await _job_asset_nodes(
                target.dagster_location, repository["name"], pipeline["name"]
            )
            assets[repository["name"], pipeline["name"]] = [
                _assets([node.get("assetKey")])[0] for node in nodes
            ]
            definitions = [_definition_metadata(node) for node in nodes]
            metadata = _definition_metadata(pipeline)
            for key in ("domain", "group", "source"):
                declared = {item[key] for item in definitions if key in item}
                if key not in metadata and len(declared) == 1:
                    metadata[key] = declared.pop()
            pipeline["phlo_metadata"] = metadata
            pipeline["phlo_owners"] = _definition_owners(pipeline) or sorted(
                {owner for node in nodes for owner in _definition_owners(node)}
            )
            pipeline["phlo_release"] = _feeds_release(metadata) or any(
                _feeds_release(item) for item in definitions
            )
    return target, repositories, assets


async def _job_asset_nodes(location: str, repository: str, job: str) -> list[dict[str, Any]]:
    result = await _graphql(
        ASSET_JOBS_QUERY,
        {
            "pipeline": {
                "repositoryLocationName": location,
                "repositoryName": repository,
                "pipelineName": job,
            }
        },
    )
    data = result.get("data")
    nodes = data.get("assetNodes") if isinstance(data, dict) else None
    if not isinstance(nodes, list) or len(nodes) > _INVENTORY_LIMIT:
        raise BadGatewayError("Dagster returned an invalid asset-job inventory.")
    for node in nodes:
        repo = node.get("repository") if isinstance(node, dict) else None
        origin = repo.get("location") if isinstance(repo, dict) else None
        if (
            not isinstance(repo, dict)
            or repo.get("name") != repository
            or not isinstance(origin, dict)
            or origin.get("name") != location
        ):
            raise BadGatewayError("Dagster returned an asset outside the selected job scope.")
    return nodes


def _jobs(
    repositories: list[dict[str, Any]],
    env: Environment,
    assets: dict[tuple[str, str], list[list[str]]],
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
            metadata = pipeline.get("phlo_metadata", {})
            items.append(
                Job(
                    id=job_id,
                    repository_name=name,
                    domain=metadata.get("domain", metadata.get("group")),
                    owners=pipeline.get("phlo_owners", []),
                    source=metadata.get("source"),
                    feeds_batch_release=pipeline.get("phlo_release", False),
                    description=pipeline.get("description")
                    if isinstance(pipeline.get("description"), str)
                    else None,
                    selected_assets=assets.get((name, job_id), []),
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
    request: Request,
    env: Environment = Query(),
    limit: Limit = 100,
    job_id: str | None = None,
    status: RunStatus | None = None,
    cursor: Annotated[str | None, Query(max_length=4096)] = None,
) -> RunPage:
    target = _target(
        request, env, allowed_query=frozenset({"env", "limit", "job_id", "status", "cursor"})
    )
    scope = [env, target.dagster_location, target.nessie_ref, job_id, status]
    after = None
    if cursor is not None:
        try:
            payload = json.loads(base64.urlsafe_b64decode(cursor))
            if (
                payload["scope"] != scope
                or not isinstance(payload["after"], str)
                or not payload["after"]
            ):
                raise ValueError
            after = payload["after"]
        except (ValueError, TypeError, KeyError, binascii.Error) as exc:
            raise HTTPException(
                status_code=422, detail="Run cursor does not match environment and filters."
            ) from exc
    items, following = await _run_page(
        target.dagster_location, target.nessie_ref, limit, after, job_id, status
    )
    token = (
        base64.urlsafe_b64encode(json.dumps({"scope": scope, "after": following}).encode()).decode()
        if following
        else None
    )
    return RunPage(env=env, items=items, next_cursor=token)


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

    def parse_error(error: Any, depth: int = 0) -> RunError | None:
        if error is None:
            return None
        if not isinstance(error, dict) or not isinstance(error.get("message"), str):
            raise BadGatewayError("Dagster returned invalid run error details.")
        stack = error.get("stack", [])
        causes = error.get("causes", [])
        class_name = error.get("className")
        if (
            not isinstance(stack, list)
            or any(not isinstance(line, str) for line in stack)
            or not isinstance(causes, list)
            or (class_name is not None and not isinstance(class_name, str))
        ):
            raise BadGatewayError("Dagster returned invalid run error details.")
        return RunError(
            message=error["message"][:20_000],
            class_name=class_name,
            stack=[line[:4_000] for line in stack[:100]],
            causes=[cause for item in causes[:10] if (cause := parse_error(item, depth + 1))]
            if depth < 2
            else [],
        )

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
        level = row.get("level")
        file_key = row.get("fileKey")
        step_keys = row.get("stepKeys") or []
        if (
            (level is not None and not isinstance(level, str))
            or (file_key is not None and not isinstance(file_key, str))
            or not isinstance(step_keys, list)
            or any(not isinstance(key, str) for key in step_keys)
        ):
            raise BadGatewayError("Dagster returned an invalid run event.")
        error = parse_error(row.get("error"))
        events.append(
            RunEvent(
                event_type=event_type,
                message=row["message"][:20_000],
                timestamp=observed,
                step_key=step_key,
                level=level,
                error=error,
                captured_file_key=file_key,
                captured_step_keys=step_keys[:100],
            )
        )
    return events, has_more, cursor


def _bounded_log_text(value: str | None) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    encoded = value.encode("utf-8")
    return (
        encoded[:_CAPTURED_LOG_BYTES].decode("utf-8", errors="ignore"),
        len(encoded) > _CAPTURED_LOG_BYTES,
    )


async def _captured_logs(run_id: str, file_key: str, step_keys: list[str]) -> CapturedRunLogs:
    try:
        response = await _graphql(RUN_CAPTURED_LOGS_QUERY, {"runId": run_id, "fileKey": file_key})
    except Exception:
        return CapturedRunLogs(
            file_key=file_key,
            step_keys=step_keys,
            stdout=None,
            stderr=None,
            available=False,
        )
    data = response.get("data")
    run = data.get("runOrError") if isinstance(data, dict) else None
    logs = run.get("capturedLogs") if isinstance(run, dict) else None
    if response.get("errors") or not isinstance(logs, dict):
        return CapturedRunLogs(
            file_key=file_key,
            step_keys=step_keys,
            stdout=None,
            stderr=None,
            available=False,
        )
    stdout, stderr = logs.get("stdout"), logs.get("stderr")
    if any(value is not None and not isinstance(value, str) for value in (stdout, stderr)):
        raise BadGatewayError("Dagster returned invalid captured logs.")
    stdout, stdout_truncated = _bounded_log_text(stdout)
    stderr, stderr_truncated = _bounded_log_text(stderr)
    return CapturedRunLogs(
        file_key=file_key,
        step_keys=step_keys,
        stdout=stdout,
        stderr=stderr,
        available=stdout is not None or stderr is not None,
        truncated=stdout_truncated or stderr_truncated,
    )


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
    capture_events: dict[str, list[str]] = {}
    for event in items:
        if event.event_type == "LOGS_CAPTURED" and event.captured_file_key:
            capture_events.setdefault(event.captured_file_key, event.captured_step_keys)
    captured = await asyncio.gather(
        *(
            _captured_logs(run.run_id, file_key, step_keys)
            for file_key, step_keys in list(capture_events.items())[:20]
        )
    )
    return RunLogs(
        env=env,
        run_id=run.run_id,
        items=items,
        truncated=truncated,
        next_cursor=cursor or None,
        status=run.status,
        is_terminal=run.status in {"SUCCESS", "FAILURE", "CANCELED"},
        captured_logs=captured,
        captured_logs_available=all(item.available for item in captured),
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
            target.dagster_location, target.nessie_ref, _PATTERN_SCAN_LIMIT, job_id
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
            target.dagster_location, target.nessie_ref, _PATTERN_SCAN_LIMIT, job_id
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
    from phlo.plugins.observatory_settings import StorageUnavailableError
    from phlo_api.observatory_api.settings import get_operational_maintenance_windows

    _target(request, env, allowed_query=frozenset({"env"}))
    raw = get_process_settings().phlo_v1_maintenance_windows
    windows = []
    try:
        if raw:
            if len(raw) > 65_536:
                raise ValueError
            windows = json.loads(raw)[env]
            if not isinstance(windows, list) or len(windows) > 100:
                raise ValueError
        operational = await run_sync(get_operational_maintenance_windows, env)
        if operational is not None:
            if not isinstance(operational, list) or len(operational) > 100:
                raise ValueError
            # Operator-defined windows win when the same policy identity appears twice.
            operator_ids = {window["id"] for window in windows}
            windows = windows + [
                window for window in operational if window["id"] not in operator_ids
            ]
        if not raw and operational is None:
            return MaintenanceWindowPage(env=env, status="unavailable", items=[])
        items = [MaintenanceWindow.model_validate_json(json.dumps(item)) for item in windows]
        if any(item.ends_at <= item.starts_at for item in items) or len(
            {item.id for item in items}
        ) != len(items):
            raise ValueError
    except (KeyError, TypeError, ValueError, StorageUnavailableError) as exc:
        raise BackendUnavailableError("Configured maintenance windows are unavailable.") from exc
    return MaintenanceWindowPage(env=env, status="configured", items=items)


def _require_single_replica_actions() -> None:
    settings = get_deployment_settings()
    if (
        not settings.actions_single_replica
        or not settings.actions_single_process
        or not settings.actions_ref_tag_contract
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


@router.post(
    "/jobs/{job_id}/launch",
    response_model=ActionResult[JobLaunchResult | SkippedActionResult],
)
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
                        + (
                            [{"key": "dagster/partition", "value": payload.partition_key}]
                            if payload.partition_key is not None
                            else []
                        )
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


@router.post("/schedules/{schedule_id}/{action}", response_model=ActionResult[ScheduleActionResult])
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


@router.post(
    "/runs/{run_id}/cancel",
    response_model=ActionResult[ProviderActionResult | SkippedActionResult],
)
async def v1_run_cancel(
    request: Request, run_id: str, payload: RunActionRequest, env: Environment = Query()
) -> ActionResult:
    return await _run_action(request, run_id, payload, env, "cancel")


@router.post(
    "/runs/{run_id}/retry",
    response_model=ActionResult[ProviderActionResult | SkippedActionResult],
)
async def v1_run_retry(
    request: Request, run_id: str, payload: RunActionRequest, env: Environment = Query()
) -> ActionResult:
    return await _run_action(request, run_id, payload, env, "retry")


def _incident_asset_graph(
    nodes: Any, location_name: str
) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]]]:
    """Validate scoped graph identities and derive aliases from exact key paths."""
    if not isinstance(nodes, list) or len(nodes) > _INVENTORY_LIMIT:
        raise BadGatewayError("Dagster returned an invalid asset graph.")
    graph: dict[str, dict[str, Any]] = {}
    aliases: dict[str, set[str]] = {}
    for node in nodes:
        repo = node.get("repository") if isinstance(node, dict) else None
        if not isinstance(repo, dict):
            raise BadGatewayError("Dagster returned an invalid asset graph repository.")
        location = repo.get("location")
        if not isinstance(location, dict) or not isinstance(location.get("name"), str):
            raise BadGatewayError("Dagster returned an invalid asset graph identity.")
        if location["name"] != location_name:
            continue
        if (
            not isinstance(repo.get("name"), str)
            or not isinstance(node.get("dependencyKeys"), list)
            or not isinstance(node.get("jobNames"), list)
        ):
            raise BadGatewayError("Dagster returned an incomplete asset graph.")
        path = _assets([node.get("assetKey")])[0]
        asset_id = "/".join(path)
        if asset_id in graph:
            raise BadGatewayError("Dagster returned ambiguous asset identities.")
        graph[asset_id] = node
        for alias in {asset_id, ".".join(path)}:
            aliases.setdefault(alias, set()).add(asset_id)
    return graph, aliases


async def preflight_incident_pause(
    request: Request, *, env: Environment, asset_ids: list[str]
) -> list[dict[str, Any]]:
    """Authorize and resolve exact downstream gold schedules before persisting an effect."""
    from phlo_api.api.operation_controls import require_scope
    from phlo_api.api.v1_assets import _asset_nodes

    auth = require_scope(request, "lakehouse:operate")
    _require_single_replica_actions()
    target = _target(request, env, allowed_query=frozenset({"env"}))
    repositories = _repositories(await _graphql(JOBS_QUERY), target.dagster_location)
    nodes = await _asset_nodes(request, env)
    graph, aliases = _incident_asset_graph(nodes, target.dagster_location)
    if not asset_ids or any(asset_id not in aliases for asset_id in asset_ids):
        raise NotFoundError("Affected assets were not found in the selected environment.")
    if any(len(aliases[asset_id]) != 1 for asset_id in asset_ids):
        raise ConflictError("Affected asset identifier is ambiguous in the selected environment.")
    seeds = {next(iter(aliases[asset_id])) for asset_id in asset_ids}
    reached = set(seeds)
    while True:
        following = {
            key
            for key, node in graph.items()
            if any("/".join(path) in reached for path in _assets(node["dependencyKeys"]))
        }
        if following.issubset(reached):
            break
        reached.update(following)
    gold = {
        key: node
        for key, node in graph.items()
        if key in reached - seeds
        and _definition_metadata(node).get("phlo/layer", node.get("groupName")) == "gold"
    }
    targets: list[dict[str, Any]] = []
    for repo in repositories:
        for schedule in repo.get("schedules", []):
            if not isinstance(schedule, dict) or not isinstance(
                schedule.get("scheduleState"), dict
            ):
                raise BadGatewayError("Dagster returned an invalid schedule inventory.")
            affected = sorted(
                key
                for key, node in gold.items()
                if node["repository"]["name"] == repo["name"]
                and schedule.get("pipelineName") in node["jobNames"]
            )
            if not affected:
                continue
            if schedule["scheduleState"].get("status") not in {"RUNNING", "STOPPED"}:
                raise BackendUnavailableError("Downstream schedule state is unavailable.")
            targets.append(
                {
                    "schedule_id": schedule["name"],
                    "repository_name": repo["name"],
                    "job_id": schedule["pipelineName"],
                    "asset_ids": affected,
                    "affected_asset_ids": sorted(seeds),
                    "env": env,
                    "location": target.dagster_location,
                    "ref": target.nessie_ref,
                    "subject": auth["subject"],
                    "expected_status": schedule["scheduleState"]["status"],
                }
            )
    return sorted(targets, key=lambda item: (item["repository_name"], item["schedule_id"]))


async def pause_incident_downstream(
    request: Request,
    *,
    env: Environment,
    incident_id: str,
    effect_id: str,
    targets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Retry only authorized persisted targets, returning confirmed per-schedule outcomes."""
    from phlo_api.api.operation_controls import require_scope
    from phlo_api.errors import PhloApiError

    auth = require_scope(request, "lakehouse:operate")
    _require_single_replica_actions()
    scope = _target(request, env, allowed_query=frozenset({"env"}))
    if not incident_id or not effect_id:
        raise HTTPException(status_code=422, detail="Incident effect identity is required.")
    outcomes: list[dict[str, Any]] = []
    for persisted in targets:
        outcome = {
            "schedule_id": persisted.get("schedule_id"),
            "asset_ids": persisted.get("asset_ids", []),
        }
        try:
            if (
                persisted.get("env") != env
                or persisted.get("location") != scope.dagster_location
                or persisted.get("ref") != scope.nessie_ref
                or persisted.get("subject") != auth["subject"]
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Incident schedule target does not match the initiating principal and environment.",
                )
            current = await preflight_incident_pause(
                request, env=env, asset_ids=persisted["affected_asset_ids"]
            )
            matches = [
                item
                for item in current
                if all(
                    item[key] == persisted.get(key)
                    for key in ("schedule_id", "repository_name", "job_id", "asset_ids")
                )
            ]
            if len(matches) != 1:
                raise ConflictError(
                    "Downstream schedule definition changed; effect was not applied."
                )
            if matches[0]["expected_status"] == "STOPPED":
                outcomes.append({**outcome, "status": "already_paused"})
                continue
            key = hashlib.sha256(
                json.dumps(
                    [
                        incident_id,
                        effect_id,
                        env,
                        scope.dagster_location,
                        scope.nessie_ref,
                        persisted["repository_name"],
                        persisted["schedule_id"],
                    ]
                ).encode()
            ).hexdigest()
            result = await v1_schedule_action(
                request,
                persisted["schedule_id"],
                "pause",
                ScheduleActionRequest(
                    idempotency_key=key,
                    expected_status=persisted["expected_status"],
                    confirmed=True,
                ),
                env,
            )
            if result.status != "accepted" or result.result.get("schedule_status") != "STOPPED":
                raise BackendUnavailableError("Dagster did not confirm the schedule pause.")
            confirmed = await preflight_incident_pause(
                request, env=env, asset_ids=persisted["affected_asset_ids"]
            )
            if not any(
                item["schedule_id"] == persisted["schedule_id"]
                and item["repository_name"] == persisted["repository_name"]
                and item["expected_status"] == "STOPPED"
                for item in confirmed
            ):
                raise ConflictError(
                    "Schedule is not paused after the action; effect remains unconfirmed."
                )
            outcomes.append({**outcome, "status": "paused"})
        except (HTTPException, PhloApiError, KeyError, TypeError, ValueError) as exc:
            outcomes.append({**outcome, "status": "failed", "error": str(exc)})
    return outcomes
