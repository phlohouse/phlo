"""Read-only WAP observations, scoped by Dagster location and launch identity."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from anyio.to_thread import run_sync
from fastapi import APIRouter, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from phlo.config.process import get_process_settings
from phlo_api.api import v1_jobs
from phlo_api.api.operation_controls import require_scope
from phlo_api.api.v1 import _target
from phlo_api.api.v1_branch_workflows import _nessie
from phlo_api.errors import BackendUnavailableError, BadGatewayError
from phlo_api.v1_contract import Environment, RunStatus, WireModel

router = APIRouter(tags=["v1 WAP"])
_RUN_LIMIT = 500
_JSON_LIMIT = 2 * 1024 * 1024
_LOGICAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,114}$")


class WapRun(WireModel):
    logical_run_id: str
    run_id: str
    job_id: str
    status: RunStatus
    created_at: AwareDatetime
    staging_ref: str
    catalog_system: str | None
    strategy: Literal["branch", "snapshot", "unknown"]
    branch_state: Literal["present", "absent", "unknown", "not_applicable"]
    branch_hash: str | None
    report_state: Literal["verified", "missing", "invalid"]
    lifecycle_status: str | None
    reported_at: AwareDatetime | None


class WapRunPage(WireModel):
    env: Environment
    items: list[WapRun]
    scanned_runs: int
    scan_limit: int
    scan_limited: bool
    catalog_available: bool


class _Report(BaseModel):
    model_config = ConfigDict(strict=True)
    schema_version: Literal["phlo.wap_report.v2"]
    run_id: str
    dagster_run_id: str
    branch: str
    launch_tags: dict[str, str]
    launch_manifest_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    strategy: Literal["branch", "snapshot"]
    status: str = Field(min_length=1, max_length=128)
    updated_at: AwareDatetime
    context: dict[str, str] | None = None
    target_branch: str | None = None


def _read_json(path: Path) -> bytes:
    with path.open("rb") as handle:
        value = handle.read(_JSON_LIMIT + 1)
    if len(value) > _JSON_LIMIT:
        raise ValueError("WAP evidence exceeds the read limit.")
    return value


def _report(
    logical_id: str, run_id: str, tags: dict[str, str]
) -> tuple[_Report | None, Literal["verified", "missing", "invalid"]]:
    root = (
        Path(get_process_settings().get("PHLO_PROJECT_PATH", "/app/project"))
        / ".phlo"
        / "wap-reports"
    )
    try:
        report = _Report.model_validate_json(_read_json(root / f"{logical_id}.json"))
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError, ValidationError):
        return None, "invalid"
    run_key = hashlib.sha256(logical_id.encode()).hexdigest()[:24]
    try:
        raw = _read_json(root / "launches" / f"{run_key}.{report.launch_manifest_checksum}.json")
        binding = json.loads(raw)
        valid = (
            hashlib.sha256(raw).hexdigest() == report.launch_manifest_checksum
            and isinstance(binding, dict)
            and binding.get("schema_version")
            in {"phlo.wap_launch_manifest.v1", "phlo.wap_launch_manifest.v2"}
            and binding.get("logical_run_id") == logical_id == report.run_id
            and binding.get("dagster_run_id") == run_id == report.dagster_run_id
            and binding.get("branch") == tags["phlo/wap_branch"] == report.branch
            and binding.get("tags") == report.launch_tags
            and all(tags.get(key) == value for key, value in report.launch_tags.items())
            and all(
                report.launch_tags.get(key) == tags.get(key)
                for key in ("phlo/run_id", "phlo/wap_branch", "phlo/ref", "phlo/project_id")
            )
        )
        if valid and binding["schema_version"] == "phlo.wap_launch_manifest.v2":
            context = binding.get("context")
            valid = (
                isinstance(context, dict)
                and set(context)
                == {"environment", "target_ref", "location", "backend_id", "strategy"}
                and all(isinstance(value, str) and value for value in context.values())
                and context == report.context
                and context["target_ref"] == binding.get("target_branch") == report.target_branch
                and context["strategy"] == report.strategy
                and re.fullmatch(r"[a-f0-9]{64}", context["backend_id"]) is not None
                and all(
                    tags.get(key, context["environment"]) == context["environment"]
                    for key in ("environment", "env")
                )
                and all(tags.get(key, report.branch) == report.branch for key in ("ref", "branch"))
            )
    except (OSError, ValueError, UnicodeDecodeError):
        return None, "invalid"
    return (report, "verified") if valid else (None, "invalid")


def _publication_ref(row: dict[str, Any], *, promoted_only: bool = False) -> str | None:
    """Correlate WAP's staging run to its immutable configured publication target."""
    from phlo_api.api.v1 import _targets

    tags = _launch_tags(row)
    logical_id = tags.get("phlo/run_id", "")
    branch = tags.get("phlo/wap_branch")
    if (
        not _LOGICAL_ID.fullmatch(logical_id)
        or branch != f"pipeline-run-{logical_id}"
        or tags.get("phlo/ref") != branch
        or not tags.get("phlo/project_id")
    ):
        return None
    run_id = row.get("runId")
    if not isinstance(run_id, str) or not run_id:
        return None
    report, state = _report(logical_id, run_id, tags)
    if state != "verified" or report is None or report.context is None:
        return None
    context = report.context
    target = _targets().get(context["environment"])
    origin = row.get("repositoryOrigin")
    if (
        target is None
        or target.nessie_ref != context["target_ref"]
        or target.dagster_location != context["location"]
        or not isinstance(origin, dict)
        or origin.get("repositoryLocationName") != context["location"]
        or (promoted_only and report.status != "promoted")
    ):
        return None
    return target.nessie_ref


def _launch_tags(row: Any) -> dict[str, str]:
    if not isinstance(row, dict) or not isinstance(row.get("tags"), list):
        raise BadGatewayError("Dagster returned invalid run tags.")
    tags: dict[str, str] = {}
    for tag in row["tags"]:
        if (
            not isinstance(tag, dict)
            or not isinstance(tag.get("key"), str)
            or not isinstance(tag.get("value"), str)
            or tag["key"] in tags
        ):
            raise BadGatewayError("Dagster returned invalid run tags.")
        tags[tag["key"]] = tag["value"]
    return tags


async def _references() -> tuple[dict[str, str], bool]:
    try:
        payload = await _nessie("GET", "/api/v1/trees", params={"fetch": "ALL"})
    except (BackendUnavailableError, BadGatewayError):
        return {}, False
    if not isinstance(payload, dict):
        return {}, False
    rows = payload.get("references")
    if not isinstance(rows, list) or len(rows) > 10_000:
        return {}, False
    references: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            return {}, False
        name, revision, kind = row.get("name"), row.get("hash"), row.get("type")
        if not isinstance(name, str) or not isinstance(revision, str) or not revision:
            return {}, False
        if kind == "BRANCH":
            if name in references:
                return {}, False
            references[name] = revision
        elif kind != "TAG":
            return {}, False
    # Nessie may paginate its reference list. Absence is only known for a complete list.
    return references, payload.get("hasMore", False) is False


async def _observation(
    row: Any, location: str, references: dict[str, str], catalog_available: bool, ref: str
) -> WapRun | None:
    tags = _launch_tags(row)
    staging_ref = tags.get("phlo/wap_branch")
    if staging_ref is None:
        return None
    origin = row.get("repositoryOrigin")
    if not isinstance(origin, dict) or not isinstance(origin.get("repositoryLocationName"), str):
        raise BadGatewayError("WAP run has no environment identity.")
    if origin["repositoryLocationName"] != location:
        return None
    logical_id = tags.get("phlo/run_id", "")
    if (
        not _LOGICAL_ID.fullmatch(logical_id)
        or staging_ref != f"pipeline-run-{logical_id}"
        or tags.get("phlo/ref") != staging_ref
        or not tags.get("phlo/project_id")
    ):
        raise BadGatewayError("WAP run has inconsistent launch identity.")
    run = v1_jobs._run(row, location, staging_ref)
    assert run is not None
    report, report_state = await run_sync(_report, logical_id, run.run_id, tags)
    if (
        (report is not None and report.context is not None)
        or "phlo/wap_binding" in tags
        or "environment" in tags
    ) and _publication_ref(row) != ref:
        return None
    catalog_system = tags.get("phlo/catalog_system")
    strategy: Literal["branch", "snapshot", "unknown"] = report.strategy if report else "unknown"
    branch_state: Literal["present", "absent", "unknown", "not_applicable"] = "unknown"
    branch_hash = None
    if strategy == "snapshot":
        branch_state = "not_applicable"
    elif catalog_system == "nessie":
        branch_hash = references.get(staging_ref)
        if branch_hash:
            strategy, branch_state = "branch", "present"
        elif catalog_available:
            branch_state = "absent"
    return WapRun(
        logical_run_id=logical_id,
        run_id=run.run_id,
        job_id=run.job_id,
        status=run.status,
        created_at=run.created_at,
        staging_ref=staging_ref,
        catalog_system=catalog_system,
        strategy=strategy,
        branch_state=branch_state,
        branch_hash=branch_hash,
        report_state=report_state,
        lifecycle_status=report.status if report else None,
        reported_at=report.updated_at if report else None,
    )


@router.get("/wap/runs", response_model=WapRunPage)
async def v1_wap_runs(request: Request, env: Environment = Query()) -> WapRunPage:
    target = _target(request, env)
    require_scope(request, "lakehouse:read")
    result = await v1_jobs._graphql(v1_jobs.RUNS_QUERY, {"limit": _RUN_LIMIT})
    rows = v1_jobs._field(result, "runsOrError", "Runs").get("results")
    if not isinstance(rows, list) or len(rows) > _RUN_LIMIT:
        raise BadGatewayError("Dagster returned an invalid WAP run inventory.")
    references, catalog_available = await _references()
    items = []
    seen: set[str] = set()
    for row in rows:
        item = await _observation(
            row, target.dagster_location, references, catalog_available, target.nessie_ref
        )
        if item is not None:
            if item.run_id in seen:
                raise BadGatewayError("Dagster returned duplicate WAP runs.")
            seen.add(item.run_id)
            items.append(item)
    return WapRunPage(
        env=env,
        items=sorted(items, key=lambda item: (item.created_at, item.run_id), reverse=True),
        scanned_runs=len(rows),
        scan_limit=_RUN_LIMIT,
        scan_limited=len(rows) == _RUN_LIMIT,
        catalog_available=catalog_available,
    )
