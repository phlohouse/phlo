"""Provider-neutral Observatory run read model.

Legacy Dagster runs degrade to an empty list when the backend is unreachable so
the view renders "no runs" instead of failing the request. Only rows sourced
from complete durable run evidence carry a ``report_identity``; legacy,
manifest, and recovered-operation rows never do.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from phlo_api.observatory_api.observatory_models import (
    ObservatoryRun,
    ObservatoryRunReportIdentity,
    RunStatus,
    ObservatoryResourceRef,
)

_DAGSTER_STATUS_MAP: dict[str, RunStatus] = {
    "SUCCESS": "succeeded",
    "FAILURE": "failed",
    "STARTED": "running",
    "QUEUED": "queued",
    "CANCELED": "cancelled",
}

_DURABLE_STATUS_MAP: dict[str, RunStatus] = {
    "success": "succeeded",
    "succeeded": "succeeded",
    "failed": "failed",
    "failure": "failed",
    "error": "failed",
    "running": "running",
    "started": "running",
    "queued": "queued",
    "canceling": "running",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "skipped": "cancelled",
}


def load_runs() -> list[ObservatoryRun]:
    """Load provider-neutral orchestrator runs."""
    # Degrade to an empty list when the legacy Dagster backend is unreachable or
    # errors; the read model renders "no runs" instead of failing the request.
    try:
        legacy_runs = asyncio.run(_load_legacy_dagster_runs())
    except Exception:
        return []

    return [_normalize_legacy_dagster_run(run) for run in legacy_runs]


def load_durable_runs(
    store: Any, *, limit: int, cursor: str | None
) -> tuple[list[ObservatoryRun], str | None]:
    """Load runs from complete canonical durable run evidence.

    Only rows sourced from the durable run-evidence store carry a
    ``report_identity``. Legacy Dagster rows, manifest rows, and recovered
    operation rows never receive one.
    """
    rows, next_cursor = store.list_runs_page(limit=limit, cursor=cursor)

    runs: list[ObservatoryRun] = []
    for row in rows:
        identity = _durable_report_identity(row)
        if identity is None:
            continue
        runs.append(_durable_run_from_row(row, identity))
    return runs, next_cursor


def _durable_report_identity(row: Mapping[str, Any]) -> ObservatoryRunReportIdentity | None:
    project_id = row.get("project_id")
    run_id = row.get("run_id")
    attempt = row.get("attempt")
    if not isinstance(project_id, str) or not project_id.strip():
        return None
    if not isinstance(run_id, str) or not run_id.strip():
        return None
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        return None
    return ObservatoryRunReportIdentity(
        project_id=project_id,
        run_id=run_id,
        attempt=attempt,
    )


def _durable_run_from_row(
    row: Mapping[str, Any], identity: ObservatoryRunReportIdentity
) -> ObservatoryRun:
    started_at = _canonical_timestamp(row.get("started_at"))
    completed_at = _canonical_timestamp(row.get("finished_at"))
    pipeline_name = row.get("pipeline_name")
    name = (
        str(pipeline_name)
        if isinstance(pipeline_name, str) and pipeline_name
        else (f"{identity.project_id}/{identity.run_id}")
    )
    return ObservatoryRun(
        id=f"{identity.project_id}/{identity.run_id}",
        name=name,
        status=_durable_status(row.get("status")),
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=_duration_seconds(started_at, completed_at),
        metadata={"source": "durable_run_evidence"},
        report_identity=identity,
    )


def _durable_status(status: Any) -> RunStatus:
    if status is None:
        return "unknown"
    return _DURABLE_STATUS_MAP.get(str(status).strip().lower(), "unknown")


def _canonical_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()
    parsed = _parse_timestamp(str(value))
    return parsed.astimezone(UTC).isoformat() if parsed else str(value)


async def _load_legacy_dagster_runs() -> list[dict[str, Any]]:
    from phlo_api.observatory_api.dagster import get_runs

    runs = await get_runs()
    return runs if isinstance(runs, list) else []


def _normalize_legacy_dagster_run(run: Mapping[str, Any]) -> ObservatoryRun:
    started_at = _optional_str(run.get("startTime"))
    completed_at = _optional_str(run.get("endTime"))

    return ObservatoryRun(
        id=_first_str(run, ("id", "runId"), "unknown"),
        name=_first_str(run, ("jobName", "pipelineName"), "run"),
        status=_normalize_status(run.get("status")),
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=_duration_seconds(started_at, completed_at),
        assets=_normalize_asset_refs(run.get("assetKeys")),
        metadata={"source": "dagster"},
    )


def _first_str(run: Mapping[str, Any], keys: tuple[str, ...], default: str) -> str:
    for key in keys:
        value = run.get(key)
        if value:
            return str(value)
    return default


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, UTC).isoformat()
    return str(value)


def _normalize_status(status: Any) -> RunStatus:
    if status is None:
        return "unknown"
    return _DAGSTER_STATUS_MAP.get(str(status).upper(), "unknown")


def _normalize_asset_refs(asset_keys: Any) -> list[ObservatoryResourceRef]:
    if not isinstance(asset_keys, Sequence) or isinstance(asset_keys, (str, bytes)):
        return []

    refs: list[ObservatoryResourceRef] = []
    for asset_key in asset_keys:
        if not isinstance(asset_key, Sequence) or isinstance(asset_key, (str, bytes)):
            continue
        asset_id = ".".join(str(part) for part in asset_key if part is not None)
        if asset_id:
            refs.append(ObservatoryResourceRef(kind="asset", id=asset_id, label=asset_id))
    return refs


def _duration_seconds(started_at: str | None, completed_at: str | None) -> float | None:
    if not started_at or not completed_at:
        return None

    try:
        started = _parse_timestamp(started_at)
        completed = _parse_timestamp(completed_at)
    except ValueError:
        return None

    return (completed - started).total_seconds()


def _parse_timestamp(value: str) -> datetime:
    try:
        return datetime.fromtimestamp(float(value), UTC)
    except ValueError:
        pass
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
