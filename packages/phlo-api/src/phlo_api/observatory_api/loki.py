"""Loki Log Querying API Router.

Endpoints for querying logs from Loki.
Supports correlation by run_id, asset_key, job_name, and partition_key.

This module provides a unified interface to query structured logs from
the Loki log aggregation system. It supports filtering by various
correlation IDs to enable debugging of data pipelines and operations.

Key Endpoints:
    GET /connection: Check Loki connectivity.
    GET /query: Query logs with filters.
    GET /runs/{run_id}: Query logs for a specific Dagster run.
    GET /assets/{asset_key}: Query logs for a specific asset.
    GET /labels: Get available log label keys.

Environment Variables:
    LOKI_URL: URL for the Loki server.

Example:
    Querying logs for a Dagster run:

    .. code-block:: bash

        curl http://localhost:4000/api/loki/runs/abc-123

"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import re
from datetime import datetime, timedelta
from multiprocessing.connection import Connection
from time import monotonic
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from phlo.config.env import project_env_value
from phlo.config.network import resolve_url
from phlo.logging import get_logger
from phlo_api.pagination import decode_cursor, paginate_items

logger = get_logger(__name__)

router = APIRouter(tags=["loki"])

DEFAULT_LOKI_URL = "http://loki:3100"
MAX_REGEX_PATTERN_LENGTH = 512
REGEX_EVALUATION_TIMEOUT_SECONDS = 0.1

LogLevel = Literal["debug", "info", "warn", "error"]

_LOKI_URL_OVERRIDE_ERROR = {
    "error": "loki_url_override_not_allowed",
    "message": "Caller-supplied loki_url is not allowed; use the operator-configured Loki endpoint.",
}
_INVALID_REGEX_ERROR = {"error": "invalid_regex"}
_REGEX_EVALUATION_TIMEOUT_ERROR = {"error": "regex_evaluation_timeout"}


class _InvalidRegexError(Exception):
    """Raised when a message-filter regex is invalid."""


class _RegexEvaluationTimeoutError(Exception):
    """Raised when message-filter regex evaluation exceeds its budget."""


def _regex_filter_worker(
    result_connection: Connection, pattern_text: str, messages: list[str]
) -> None:
    """Evaluate a regex filter in an isolated process.

    The parent enforces the deadline and terminates this process if Python's
    backtracking engine takes too long. Only matching indexes cross the process
    boundary so worker failures can never include log contents in an error.
    """
    try:
        pattern = re.compile(pattern_text)
    except re.error:
        result_connection.send(("invalid", []))
    else:
        result_connection.send(
            (
                "matches",
                [index for index, message in enumerate(messages) if pattern.search(message)],
            )
        )
    finally:
        result_connection.close()


def _filter_entries_with_regex(entries: list[LogEntry], pattern_text: str) -> list[LogEntry]:
    """Filter entries with a hard total evaluation deadline outside the API process."""
    receive_connection, send_connection = multiprocessing.Pipe(duplex=False)
    process = multiprocessing.get_context("fork").Process(
        target=_regex_filter_worker,
        args=(send_connection, pattern_text, [entry.message for entry in entries]),
    )
    deadline = monotonic() + REGEX_EVALUATION_TIMEOUT_SECONDS
    try:
        process.start()
        send_connection.close()
        remaining = deadline - monotonic()
        if remaining <= 0 or not receive_connection.poll(remaining):
            raise _RegexEvaluationTimeoutError
        status, matching_indexes = receive_connection.recv()
    except EOFError as exc:
        raise _InvalidRegexError from exc
    finally:
        receive_connection.close()
        if process.is_alive():
            process.terminate()
        process.join()

    if status == "invalid":
        raise _InvalidRegexError
    return [entries[index] for index in matching_indexes]


def resolve_loki_url() -> str:
    """Resolve the Loki base URL from server-side configuration only, taking the
    value from the environment or falling back to the Docker-internal default.
    """
    return resolve_url(
        project_env_value("LOKI_URL", DEFAULT_LOKI_URL) or DEFAULT_LOKI_URL,
        port_env_var="LOKI_PORT",
    )


def reject_request_loki_url(loki_url: str | None) -> None:
    """Reject a caller-supplied Loki URL before any outbound work.

    Raises: HTTPException with status 422 when the caller supplies ``loki_url``.
    """
    if loki_url is not None:
        raise HTTPException(status_code=422, detail=_LOKI_URL_OVERRIDE_ERROR)


# --- Pydantic Models ---


class LogEntry(BaseModel):
    """Represents a normalized log record carrying correlation metadata
    extracted from the payload.
    """

    timestamp: str
    level: LogLevel
    message: str
    metadata: dict[str, str]


class LogQueryResult(BaseModel):
    """Contains query results from Loki: the parsed entries plus ``has_more``,
    which says whether additional entries may exist beyond the returned limit.
    """

    entries: list[LogEntry]
    has_more: bool
    next_cursor: str | None = None


class LokiConnectionStatus(BaseModel):
    """Describes Loki connectivity status, including the error message when the
    connection check fails and the Loki version string when available.
    """

    connected: bool
    error: str | None = None
    version: str | None = None


# --- Helper Functions ---


def build_log_query(
    run_id: str | None = None,
    asset_key: str | None = None,
    job: str | None = None,
    partition_key: str | None = None,
    check_name: str | None = None,
    level: LogLevel | None = None,
    service: str | None = None,
) -> str:
    """Build a LogQL query with optional filters for run ID, asset key, job,
    partition key, check name, level, and service.
    """
    label_matchers = []
    line_filters = []
    json_filters = []

    # Service filter - required by Loki
    if service:
        label_matchers.append(f'container=~".*{service}.*"')
    else:
        label_matchers.append('container=~".+"')

    # Dagster's container logs include the execution run ID in their rendered
    # message, whereas application logs may expose it as a JSON field.  Filter
    # the raw line first so both log formats remain queryable.
    if run_id:
        line_filters.append(f'|= "{run_id}"')
    if asset_key:
        json_filters.append(f'asset_key="{asset_key}"')
    if job:
        json_filters.append(f'job_name="{job}"')
    if partition_key:
        json_filters.append(f'partition_key="{partition_key}"')
    if check_name:
        json_filters.append(f'check_name="{check_name}"')
    if level:
        json_filters.append(f'level="{level}"')

    label_selector = ", ".join(label_matchers)
    line_pipeline = " " + " ".join(line_filters) if line_filters else ""
    json_pipeline = " | json | " + " | ".join(json_filters) if json_filters else " | json"

    return "{" + label_selector + "}" + line_pipeline + json_pipeline


def parse_loki_response(response: dict[str, Any]) -> list[LogEntry]:
    """Parse a Loki query API response payload into log entries sorted by
    timestamp descending.
    """
    entries = []

    for stream in response.get("data", {}).get("result", []):
        stream_labels = stream.get("stream", {})
        for timestamp_ns, line in stream.get("values", []):
            try:
                parsed = json.loads(line)
                function_name = parsed.get("function") or parsed.get("fn")
                entries.append(
                    LogEntry(
                        timestamp=datetime.fromtimestamp(
                            int(timestamp_ns) / 1_000_000_000
                        ).isoformat(),
                        level=parsed.get("level", "info"),
                        message=parsed.get("msg") or parsed.get("message") or line,
                        metadata={
                            k: v
                            for k, v in {
                                "run_id": parsed.get("run_id"),
                                "asset_key": parsed.get("asset_key"),
                                "job_name": parsed.get("job_name"),
                                "partition_key": parsed.get("partition_key"),
                                "check_name": parsed.get("check_name"),
                                "service": parsed.get("service"),
                                "module": parsed.get("module"),
                                "function": function_name,
                                "fn": function_name,
                                "line": str(parsed.get("line")) if parsed.get("line") else None,
                                "trace_id": parsed.get("trace_id")
                                or parsed.get("phlo.metadata.trace_id")
                                or parsed.get("phlo_trace_id"),
                                "span_id": parsed.get("span_id")
                                or parsed.get("phlo.metadata.span_id")
                                or parsed.get("phlo_span_id"),
                                "trace_flags": parsed.get("trace_flags")
                                or parsed.get("phlo.metadata.trace_flags"),
                                "durationMs": str(parsed.get("durationMs"))
                                if parsed.get("durationMs")
                                else None,
                            }.items()
                            if v
                        },
                    )
                )
            except Exception:
                # Non-JSON log line
                entries.append(
                    LogEntry(
                        timestamp=datetime.fromtimestamp(
                            int(timestamp_ns) / 1_000_000_000
                        ).isoformat(),
                        level="info",
                        message=line,
                        metadata=stream_labels,
                    )
                )

    # Sort by timestamp descending
    entries.sort(key=lambda e: e.timestamp, reverse=True)
    return entries


# --- Internal query helpers (no request-controlled destination) ---


async def fetch_connection_status() -> LokiConnectionStatus:
    """Check whether the configured Loki endpoint is reachable, returning the
    connection state and Loki version information.
    """
    url = resolve_loki_url()

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{url}/ready")

            if response.status_code != 200:
                return LokiConnectionStatus(
                    connected=False,
                    error=f"HTTP {response.status_code}: {response.reason_phrase}",
                )

            try:
                build_response = await client.get(f"{url}/loki/api/v1/status/buildinfo")
                version = (
                    build_response.json().get("version", "unknown")
                    if build_response.status_code == 200
                    else "unknown"
                )
            except Exception:
                version = "unknown"

            return LokiConnectionStatus(connected=True, version=version)
    except Exception as e:
        return LokiConnectionStatus(connected=False, error=str(e))


async def fetch_log_entries(
    start: str,
    end: str,
    run_id: str | None = None,
    asset_key: str | None = None,
    job: str | None = None,
    partition_key: str | None = None,
    check_name: str | None = None,
    level: LogLevel | None = None,
    service: str | None = None,
    limit: int = 100,
) -> LogQueryResult | dict[str, str]:
    """Query the configured Loki endpoint with correlation filters, returning a
    result with entries and a has_more flag, or an error dictionary on failure.
    """
    url = resolve_loki_url()

    try:
        query = build_log_query(run_id, asset_key, job, partition_key, check_name, level, service)

        start_ns = int(
            datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp() * 1_000_000_000
        )
        end_ns = int(datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp() * 1_000_000_000)

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{url}/loki/api/v1/query_range",
                params={
                    "query": query,
                    "start": str(start_ns),
                    "end": str(end_ns),
                    "limit": str(limit),
                    "direction": "backward",
                },
            )
            response.raise_for_status()
            result = response.json()

            entries = parse_loki_response(result)
            return LogQueryResult(entries=entries, has_more=len(entries) == limit)
    except Exception as e:
        logger.exception("Failed to query logs")
        return {"error": str(e)}


async def fetch_run_log_entries(
    run_id: str,
    level: LogLevel | None = None,
    limit: int = 500,
    query: str | None = None,
    regex: str | None = None,
    since: str | None = None,
    until: str | None = None,
    cursor: str | None = None,
) -> LogQueryResult | dict[str, str]:
    """Query configured Loki for logs belonging to a Dagster run, returning
    paginated entries or an error dictionary. Applies optional case-insensitive
    and regular-expression message filters after fetching.

    Raises: HTTPException with status 400 for an over-long or invalid regex,
    and 422 when regex evaluation exceeds its time budget.
    """
    if regex is not None and len(regex) > MAX_REGEX_PATTERN_LENGTH:
        raise HTTPException(status_code=400, detail=_INVALID_REGEX_ERROR)

    end = datetime.fromisoformat(until.replace("Z", "+00:00")) if until else datetime.now()
    start = (
        datetime.fromisoformat(since.replace("Z", "+00:00")) if since else end - timedelta(hours=24)
    )

    offset = decode_cursor(cursor)
    query_limit = min(offset + limit, 2000)
    result = await fetch_log_entries(
        start=start.isoformat(),
        end=end.isoformat(),
        run_id=run_id,
        level=level,
        limit=query_limit,
    )
    if isinstance(result, dict):
        return result
    entries = result.entries
    if query:
        entries = [entry for entry in entries if query.lower() in entry.message.lower()]
    if regex:
        try:
            entries = await asyncio.to_thread(_filter_entries_with_regex, entries, regex)
        except _InvalidRegexError as exc:
            raise HTTPException(status_code=400, detail=_INVALID_REGEX_ERROR) from exc
        except _RegexEvaluationTimeoutError as exc:
            raise HTTPException(status_code=422, detail=_REGEX_EVALUATION_TIMEOUT_ERROR) from exc
    page, next_cursor = paginate_items(entries, limit=limit, cursor=cursor)
    return LogQueryResult(
        entries=page,
        has_more=result.has_more or next_cursor is not None,
        next_cursor=next_cursor,
    )


async def fetch_asset_log_entries(
    asset_key: str,
    partition_key: str | None = None,
    level: LogLevel | None = None,
    hours_back: int = 24,
    limit: int = 200,
) -> LogQueryResult | dict[str, str]:
    """Query configured Loki for logs belonging to an asset over a lookback
    window ending now, returning entries or an error dictionary.
    """
    end = datetime.now()
    start = end - timedelta(hours=hours_back)

    return await fetch_log_entries(
        start=start.isoformat(),
        end=end.isoformat(),
        asset_key=asset_key,
        partition_key=partition_key,
        level=level,
        limit=limit,
    )


async def fetch_log_labels() -> dict[str, Any]:
    """Fetch available label keys from the configured Loki endpoint, returning
    the label payload or an error dictionary.
    """
    url = resolve_loki_url()

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{url}/loki/api/v1/labels")
            response.raise_for_status()
            result = response.json()
            return {"labels": result.get("data", [])}
    except Exception as e:
        return {"error": str(e)}


# --- API Endpoints ---


@router.get("/connection", response_model=LokiConnectionStatus)
async def check_connection(loki_url: str | None = None) -> LokiConnectionStatus:
    """Check whether Loki is reachable.

    Raises: HTTPException with status 422 when a ``loki_url`` query parameter is
    supplied; it is never honored and exists only for explicit request
    compatibility.
    """
    reject_request_loki_url(loki_url)
    return await fetch_connection_status()


@router.get("/query", response_model=LogQueryResult | dict)
async def query_logs(
    start: str,
    end: str,
    run_id: str | None = None,
    asset_key: str | None = None,
    job: str | None = None,
    partition_key: str | None = None,
    check_name: str | None = None,
    level: LogLevel | None = None,
    service: str | None = None,
    limit: int = Query(default=100, le=1000),
    loki_url: str | None = None,
) -> LogQueryResult | dict[str, str]:
    """Query logs with correlation filters.

    Executes a LogQL query against Loki with optional filters for run_id,
    asset_key, job, partition_key, check_name, level, and service, returning a
    result with entries and a has_more flag or an error dictionary.

    Raises: HTTPException with status 422 when a ``loki_url`` query parameter is
    supplied; it is never honored and exists only for explicit request
    compatibility.
    """
    reject_request_loki_url(loki_url)
    return await fetch_log_entries(
        start=start,
        end=end,
        run_id=run_id,
        asset_key=asset_key,
        job=job,
        partition_key=partition_key,
        check_name=check_name,
        level=level,
        service=service,
        limit=limit,
    )


@router.get("/runs/{run_id}", response_model=LogQueryResult | dict)
async def query_run_logs(
    run_id: str,
    level: LogLevel | None = None,
    limit: int = Query(default=500, le=2000),
    query: str | None = None,
    regex: str | None = None,
    since: str | None = None,
    until: str | None = None,
    cursor: str | None = None,
    loki_url: str | None = None,
) -> LogQueryResult | dict[str, str]:
    """Query logs for a Dagster run, returning the paginated result or an error
    dictionary.

    Raises: HTTPException with status 422 when a ``loki_url`` query parameter is
    supplied; it is never honored and exists only for explicit request
    compatibility.
    """
    reject_request_loki_url(loki_url)
    return await fetch_run_log_entries(
        run_id=run_id,
        level=level,
        limit=limit,
        query=query,
        regex=regex,
        since=since,
        until=until,
        cursor=cursor,
    )


@router.get("/runs/{run_id}/stream")
async def stream_run_logs(
    run_id: str,
    timeout_seconds: int = Query(default=30, ge=1, le=120),
    interval_seconds: float = Query(default=2.0, ge=0.25, le=10.0),
    limit: int = Query(default=200, le=2000),
    loki_url: str | None = None,
) -> StreamingResponse:
    """Stream bounded Server-Sent Events for run logs.

    Raises: HTTPException with status 422 when ``loki_url`` is supplied.
    """
    reject_request_loki_url(loki_url)

    async def events():  # noqa: ANN202
        """Yield log events until the deadline, then a done event."""
        deadline = datetime.now() + timedelta(seconds=timeout_seconds)
        # Polling re-queries Loki from scratch each interval and Loki offers no
        # cursor, so entries are deduped by content identity (timestamp, level,
        # message) to emit each record exactly once per stream.
        seen: set[str] = set()
        while datetime.now() < deadline:
            result = await fetch_run_log_entries(run_id=run_id, limit=limit)
            if isinstance(result, LogQueryResult):
                for entry in result.entries:
                    entry_id = f"{entry.timestamp}:{entry.level}:{entry.message}"
                    if entry_id in seen:
                        continue
                    seen.add(entry_id)
                    yield f"event: log\ndata: {entry.model_dump_json()}\n\n"
            else:
                yield f"event: error\ndata: {json.dumps(result)}\n\n"
                return
            await asyncio.sleep(interval_seconds)
        yield f"event: done\ndata: {json.dumps({'run_id': run_id})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@router.get("/assets/{asset_key:path}", response_model=LogQueryResult | dict)
async def query_asset_logs(
    asset_key: str,
    partition_key: str | None = None,
    level: LogLevel | None = None,
    hours_back: int = Query(default=24, le=168),
    limit: int = Query(default=200, le=1000),
    loki_url: str | None = None,
) -> LogQueryResult | dict[str, str]:
    """Query logs for an asset over a lookback window ending now, returning the
    result or an error dictionary.

    Raises: HTTPException with status 422 when a ``loki_url`` query parameter is
    supplied; it is never honored and exists only for explicit request
    compatibility.
    """
    reject_request_loki_url(loki_url)
    return await fetch_asset_log_entries(
        asset_key=asset_key,
        partition_key=partition_key,
        level=level,
        hours_back=hours_back,
        limit=limit,
    )


@router.get("/labels", response_model=dict)
async def get_log_labels(loki_url: str | None = None) -> dict[str, Any]:
    """Get available Loki label keys, returning the label payload or an error
    dictionary.

    Raises: HTTPException with status 422 when ``loki_url`` is supplied; it is
    never honored and exists only for explicit request compatibility.
    """
    reject_request_loki_url(loki_url)
    return await fetch_log_labels()
