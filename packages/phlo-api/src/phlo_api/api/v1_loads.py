"""Bounded authorized load observations; private CSV receipts and scoped ingestion runs."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from typing import Annotated, Literal, TypedDict

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field

from phlo_api.api import v1_assets as assets, v1_jobs as jobs, v1_upload_ingestion as candidates
from phlo_api.api import v1_uploads as uploads
from phlo_api.api.authentication import get_request_principal
from phlo_api.api.v1 import _target
from phlo_api.security_manifest import HTTP_ROUTE_MANIFEST, enforce_http_operation
from phlo_api.v1_contract import Environment, EnvironmentTarget, WireModel

router = APIRouter(tags=["v1 load observations"])
MAX_RUNS = 100
MAX_ITEMS = 300


class _CsvObservationIdentity(TypedDict):
    name: str
    source: str
    asset_id: str
    relation: str
    byte_count: int
    checksum: str
    ref: str
    upload_id: str
    review_version: int
    recipe_digest: str
    upload_filename: str
    upload_created_at: datetime
    upload_actor: str


class LoadObservation(WireModel):
    id: str
    evidence_kind: Literal["csv_candidate", "csv_attempt", "ingestion_run"]
    name: str
    source: str
    asset_id: str
    relation: str | None
    status: str
    observed_at: datetime | None
    timestamp_basis: Literal[
        "receipt", "materialization", "run_created", "run_started", "run_ended", "unavailable"
    ] = "unavailable"
    run_started_at: datetime | None = None
    run_ended_at: datetime | None = None
    rows: int | None = Field(default=None, ge=0)
    rows_basis: str | None = None
    byte_count: int | None = Field(default=None, ge=0)
    checksum: str | None = None
    run_id: str | None = None
    job_id: str | None = None
    ref: str
    ref_hash: str | None = None
    snapshot_id: str | None = None
    upload_id: str | None = None
    upload_filename: str | None = None
    upload_created_at: datetime | None = None
    upload_actor: str | None = None
    review_version: int | None = None
    recipe_digest: str | None = None
    detail: str


class LoadPage(WireModel):
    env: Environment
    items: list[LoadObservation]
    observed_at: datetime
    truncated: bool
    next_cursor: str | None = None
    limitations: list[str]


class _LoadCursor(WireModel):
    scope: list[str]
    phase: Literal["csv", "runs"] = "csv"
    after: str | None = Field(default=None, max_length=4096)
    run_id: str | None = Field(default=None, max_length=512)
    asset_id: str | None = Field(default=None, max_length=512)


async def _visible(request: Request, operation: str, **params: str) -> bool:
    try:
        await enforce_http_operation(request, HTTP_ROUTE_MANIFEST[operation], params)
    except HTTPException as exc:
        if exc.status_code in {403, 404}:
            return False
        raise
    return True


async def _csv_observations(request: Request, env: Environment) -> list[LoadObservation]:
    principal = get_request_principal(request)
    if principal is None:
        raise HTTPException(401, "Authentication is required.")
    # Filter creator and environment before looking up related metadata. No raw CSV is read.
    private = {
        item.upload_id: item
        for item in uploads._records()
        if item.env == env and item.actor == principal.subject
    }
    items = []
    for receipt in candidates._all():
        upload = private.get(receipt.upload_id)
        if receipt.env != env or receipt.actor != principal.subject or upload is None:
            continue
        if not await _visible(request, "v1_asset_detail", asset_id=receipt.asset_id):
            continue
        common: _CsvObservationIdentity = {
            "name": upload.filename,
            "source": "Reviewed CSV",
            "asset_id": receipt.asset_id,
            "relation": receipt.relation,
            "byte_count": upload.byte_count,
            "checksum": receipt.checksum,
            "ref": receipt.candidate_ref,
            "upload_id": receipt.upload_id,
            "review_version": receipt.review_version,
            "recipe_digest": receipt.recipe_digest,
            "upload_filename": upload.filename,
            "upload_created_at": datetime.fromisoformat(upload.created_at),
            "upload_actor": upload.actor,
        }
        if receipt.run_id is None or await _visible(request, "v1_run", run_id=receipt.run_id):
            items.append(
                LoadObservation(
                    id=f"csv:{receipt.upload_id}:{receipt.run_id or 'prepared'}",
                    evidence_kind="csv_candidate",
                    status=receipt.status,
                    observed_at=None,
                    rows=receipt.row_count if receipt.loaded else None,
                    rows_basis="Verified candidate table total" if receipt.loaded else None,
                    run_id=receipt.run_id,
                    job_id=receipt.job_name,
                    ref_hash=receipt.candidate_hash,
                    snapshot_id=receipt.snapshot_id,
                    detail=(
                        "Recorded private receipt; exact run and candidate snapshot were verified before Loaded. "
                        "Open the upload to recheck current evidence. Main is separate."
                        if receipt.loaded
                        else "Recorded candidate state; this is not evidence that rows were loaded. "
                        "Open the upload to refresh the exact run."
                    ),
                    **common,
                )
            )
        for attempt in receipt.attempts:
            if not await _visible(request, "v1_run", run_id=attempt.run_id):
                continue
            items.append(
                LoadObservation(
                    id=f"csv-attempt:{receipt.upload_id}:{attempt.run_id}",
                    evidence_kind="csv_attempt",
                    status=attempt.status.lower(),
                    observed_at=None,
                    run_id=attempt.run_id,
                    job_id=None,
                    ref_hash=attempt.launch_ref_hash,
                    detail="Retained prior attempt. Failure does not establish zero staged rows or a loaded table; "
                    "partial candidate evidence is preserved. Timestamp and loaded row count are unavailable in this receipt.",
                    **common,
                )
            )
    return items


def _ingestion_observation(asset: assets.AssetView, run: jobs.Run, ref: str) -> LoadObservation:
    write = next((item for item in asset.materializations if item.run_id == run.run_id), None)
    return LoadObservation(
        id=f"run:{run.run_id}:{asset.id}",
        evidence_kind="ingestion_run",
        name=asset.source_name or asset.id,
        source=asset.source_name or asset.compute_kind or "Ingestion",
        asset_id=asset.id,
        relation=asset.relation,
        status=f"run_{run.status.lower()}",
        observed_at=write.timestamp if write else run.ended_at or run.started_at or run.created_at,
        timestamp_basis=(
            "materialization"
            if write
            else "run_ended"
            if run.ended_at
            else "run_started"
            if run.started_at
            else "run_created"
        ),
        run_started_at=run.started_at,
        run_ended_at=run.ended_at,
        rows=write.rows_inserted if write else None,
        rows_basis="Recorded rows inserted by this asset/run"
        if write and write.rows_inserted is not None
        else None,
        run_id=run.run_id,
        job_id=run.job_id,
        ref=ref,
        ref_hash=run.tags.get("phlo/ref_hash"),
        detail="Scoped ingestion run observation, not a per-file receipt. File identity, bytes, held/rejected rows "
        "and source-load package IDs are unavailable unless separately recorded. Run status may cover other assets; "
        "a failed run alone does not establish this asset's loaded or rejected rows.",
    )


async def _run_observations(
    request: Request, inventory: list[assets.AssetView], run: jobs.Run, ref: str
) -> list[LoadObservation]:
    if not await _visible(request, "v1_run", run_id=run.run_id):
        return []
    items = []
    for asset in inventory:
        materialized = any(write.run_id == run.run_id for write in asset.materializations)
        selected = asset.key in run.selected_assets
        declared_job = run.job_id in asset.job_names and not run.selected_assets
        if (materialized or selected or declared_job) and await _visible(
            request, "v1_asset_detail", asset_id=asset.id
        ):
            items.append(_ingestion_observation(asset, run, ref))
    return sorted(items, key=lambda item: item.asset_id)


async def _ingestion_page(
    request: Request,
    target: EnvironmentTarget,
    inventory: list[assets.AssetView],
    position: _LoadCursor,
    limit: int,
) -> tuple[list[LoadObservation], _LoadCursor | None]:
    # Bound each request, not the lifetime inventory. Empty/denied raw pages still advance.
    runs, following = await jobs._run_page(
        target.dagster_location, target.nessie_ref, MAX_RUNS, position.after
    )
    items: list[LoadObservation] = []
    before = position.after
    for run in runs:
        rows = await _run_observations(request, inventory, run, target.nessie_ref)
        if run.run_id == position.run_id and position.asset_id is not None:
            rows = [row for row in rows if row.asset_id > position.asset_id]
        remaining = limit - len(items)
        if len(rows) > remaining:
            items.extend(rows[:remaining])
            return items, _LoadCursor(
                scope=position.scope,
                phase="runs",
                after=before,
                run_id=run.run_id if remaining else None,
                asset_id=rows[remaining - 1].asset_id if remaining else None,
            )
        items.extend(rows)
        before = run.run_id
    return items, (
        _LoadCursor(scope=position.scope, phase="runs", after=following) if following else None
    )


@router.get("/loads", response_model=LoadPage)
async def v1_loads(
    request: Request,
    env: Environment = Query(),
    limit: Annotated[int, Query(ge=1, le=MAX_ITEMS)] = 100,
    after: Annotated[str | None, Query(max_length=16384)] = None,
) -> LoadPage:
    allowed_query = frozenset({"env", "limit", "after"})
    target = _target(request, env, allowed_query=allowed_query)
    principal = get_request_principal(request)
    if principal is None:
        raise HTTPException(401, "Authentication is required.")
    scope = [env, target.dagster_location, target.nessie_ref, principal.subject]
    position = _LoadCursor(scope=scope)
    if after is not None:
        try:
            position = _LoadCursor.model_validate_json(base64.urlsafe_b64decode(after))
            if position.scope != scope:
                raise ValueError("scope mismatch")
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(422, "Load cursor does not match actor or environment.") from exc
    inventory = await assets._assets(request, env, allowed_query=allowed_query)
    items: list[LoadObservation] = []
    if position.phase == "csv":
        private = sorted(
            await _csv_observations(request, env), key=lambda item: item.id, reverse=True
        )
        if position.after is not None:
            private = [item for item in private if item.id < position.after]
        items = private[:limit]
        position = (
            _LoadCursor(scope=scope, after=items[-1].id)
            if len(private) > limit
            else _LoadCursor(scope=scope, phase="runs")
        )
    following: _LoadCursor | None = position
    if position.phase == "runs" and len(items) < limit:
        page, following = await _ingestion_page(
            request,
            target,
            [asset for asset in inventory if asset.is_ingestion or asset.is_source],
            position,
            limit - len(items),
        )
        items.extend(page)
    return LoadPage(
        env=env,
        items=items,
        observed_at=datetime.now(UTC),
        truncated=following is not None,
        next_cursor=(
            base64.urlsafe_b64encode(following.model_dump_json().encode()).decode()
            if following is not None
            else None
        ),
        limitations=[
            "Private CSV observations precede ingestion runs. Runs follow provider creation order, then asset ID; this is not a global load-time ordering.",
            "Each request scans up to 100 provider runs and returns at most 300 observations. Continue even after an empty page when a next cursor is present. Asset write-count history covers the last seven days.",
            "CSV receipts are creator-private recorded state; timestamps absent from the receipt remain unavailable.",
            "Ingestion observations are asset/run evidence, not invented file/extract or DLT package records. Counts are not summed across these different grains.",
            "This is a live inventory, not a frozen snapshot. Refresh to include observations added before the current cursor.",
        ],
    )
