"""Reviewed CSV candidate preparation and confirmed real Dagster ingestion."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from pydantic import Field
from phlo.workflow_authoring import create_workflow_with_provider
from phlo.operations.csv_upload import csv_output_columns
from phlo_api.api import v1_assets, v1_branch_workflows as branches, v1_uploads as uploads
from phlo_api.api.operation_controls import audit_operation, project_root, require_scope
from phlo_api.api.v1 import _target
from phlo_api.candidate_runtime import provision_candidate, verify_candidate, preview_candidate
from phlo_api.errors import BackendUnavailableError, ConflictError, NotFoundError
from phlo_api.observatory_api.dagster import (
    _dagster_operations,
    graphql_request,
    resolve_dagster_url,
)
from phlo_api.observatory_api.observatory_durable_state import load_collection, mutate_collection
from phlo_api.security_manifest import HTTP_ROUTE_MANIFEST, enforce_http_operation
from phlo_api.v1_contract import Environment, WireModel

router = APIRouter(tags=["v1 CSV candidate ingestion"])
_COLLECTION = "v1_csv_candidates"
_LOCK = asyncio.Lock()


def _require_load_runtime() -> None:
    try:
        _dagster_operations()
    except ImportError as exc:
        raise BackendUnavailableError(
            "CSV candidate loading requires 'phlo-api[dagster]'; install that extra."
        ) from exc


class DestinationRequest(WireModel):
    expected_version: int = Field(ge=1)
    table: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")


class CandidateAttempt(WireModel):
    run_id: str
    status: str
    plan_hash: str
    job_snapshot_id: str
    launch_ref_hash: str


class CandidateReceipt(WireModel):
    upload_id: str
    env: Environment
    actor: str
    review_version: int
    checksum: str
    recipe_digest: str
    table: str
    relation: str
    asset_id: str
    candidate_ref: str
    base_ref: str
    base_hash: str
    candidate_hash: str
    launch_ref_hash: str | None = None
    status: Literal["preparing", "prepared", "planned", "queued", "running", "failed", "loaded"]
    contract_digest: str | None = None
    source_digest: str | None = None
    plan_hash: str | None = None
    job_name: str | None = None
    job_snapshot_id: str | None = None
    run_id: str | None = None
    repository_name: str | None = None
    repository_location: str | None = None
    snapshot_id: str | None = None
    row_count: int | None = None
    loaded: bool = False
    main_unchanged: bool | None = None
    files: list[str] = Field(default_factory=list)
    candidate_binding: dict[str, Any] = Field(default_factory=dict)
    attempts: list[CandidateAttempt] = Field(default_factory=list, max_length=10)


class CandidateLoad(WireModel):
    expected_version: int = Field(ge=1)
    recipe_digest: str
    plan_hash: str
    confirmed: Literal[True]
    idempotency_key: str = Field(min_length=8, max_length=128)
    retry_of_run_id: str | None = None


def _legacy():
    return project_root() / ".phlo/csv-candidates.json"


def _all() -> list[CandidateReceipt]:
    values = load_collection(project_root(), _COLLECTION, _legacy())
    if len(values) > 100:
        raise BackendUnavailableError("CSV candidate collection exceeds its configured limit.")
    return [CandidateReceipt.model_validate(value) for value in values]


def _recipe(record: uploads.CsvUpload) -> str:
    value = {
        "parser_version": 1,
        "checksum": record.checksum,
        "version": record.version,
        "columns": [column.recipe() for column in record.columns],
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _receipt(
    request: Request, env: str, upload_id: str
) -> tuple[uploads.CsvUpload, CandidateReceipt]:
    record = uploads._record(request, env, upload_id)
    uploads._verified_bytes(record)
    receipt = next(
        (
            item
            for item in _all()
            if item.upload_id == upload_id and item.env == env and item.actor == record.actor
        ),
        None,
    )
    if receipt is None:
        raise NotFoundError("CSV destination has not been prepared.")
    if record.version != receipt.review_version or _recipe(record) != receipt.recipe_digest:
        raise ConflictError("The retained review differs from the immutable candidate recipe.")
    if receipt.contract_digest and receipt.source_digest:
        expected = (
            (f"contracts/uploads/{upload_id}.json", receipt.contract_digest),
            (f"workflows/uploads/{upload_id}.py", receipt.source_digest),
        )
        for relative, digest in expected:
            path = project_root() / relative
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ConflictError("An immutable CSV workflow artifact changed.")
    return record, receipt


def _save(receipt: CandidateReceipt, *, previous: CandidateReceipt | None) -> CandidateReceipt:
    def update(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        current = next((item for item in items if item.get("upload_id") == receipt.upload_id), None)
        if previous is None:
            if current is not None or len(items) >= 100:
                raise ConflictError("CSV candidate already exists or collection is full.")
            if any(item.get("asset_id") == receipt.asset_id for item in items):
                raise ConflictError(
                    "Destination asset identity is already reserved. Choose a new table."
                )
            return [*items, receipt.model_dump()]
        if (
            current is None
            or CandidateReceipt.model_validate(current).model_dump() != previous.model_dump()
        ):
            raise ConflictError("CSV candidate changed; reload before continuing.")
        return [receipt.model_dump() if item is current else item for item in items]

    mutate_collection(project_root(), _COLLECTION, _legacy(), update)
    return receipt


async def _permission(request: Request, name: str, **params: str) -> None:
    await enforce_http_operation(request, HTTP_ROUTE_MANIFEST[name], params)


async def _head(ref: str) -> str:
    reference = await branches._reference(ref)
    if not reference or reference.get("type") != "BRANCH" or not reference.get("hash"):
        raise ConflictError("Candidate reference is missing or is not a writable branch.")
    return reference["hash"]


async def _assert_head(receipt: CandidateReceipt) -> None:
    if receipt.candidate_ref == receipt.base_ref or not receipt.candidate_ref.startswith(
        f"{receipt.env}-upload-"
    ):
        raise ConflictError("Invalid candidate reference binding.")
    if (
        await verify_candidate(receipt.candidate_binding, expected_hash=receipt.candidate_hash)
        != receipt.candidate_hash
    ):
        raise ConflictError("Candidate branch changed. Prepare a new reviewed upload.")


async def _new_relation(ref: str, table: str) -> None:
    entries = await branches._nessie(
        "GET", f"/api/v1/trees/tree/{quote(ref, safe='')}/entries", params={"maxRecords": 10000}
    )
    if not isinstance(entries, dict) or entries.get("hasMore"):
        raise BackendUnavailableError("Cannot verify the complete candidate table inventory.")
    for entry in entries.get("entries", []):
        if entry.get("name", {}).get("elements") == ["bronze", table]:
            raise ConflictError("Destination already exists. Choose a new bronze table.")


async def _reload(location: str) -> None:
    value = await graphql_request(
        resolve_dagster_url(),
        "mutation Reload($name:String!){reloadRepositoryLocation(repositoryLocationName:$name){__typename ... on WorkspaceLocationEntry{loadStatus locationOrLoadError{__typename ... on RepositoryLocation{name}}}}}",
        {"name": location},
    )
    entry = value.get("data", {}).get("reloadRepositoryLocation", {})
    if (
        entry.get("loadStatus") != "LOADED"
        or entry.get("locationOrLoadError", {}).get("name") != location
    ):
        raise BackendUnavailableError(
            "CSV files were retained but Dagster reload was not acknowledged. Retry preparation."
        )


@router.post("/uploads/csv/{upload_id}/destination", response_model=CandidateReceipt)
async def v1_csv_destination(
    request: Request, upload_id: str, payload: DestinationRequest, env: Environment = Query()
) -> CandidateReceipt:
    target = _target(request, env)
    auth = uploads._upload_auth(request)
    require_scope(request, "lakehouse:operate")
    await _permission(request, "v1_csv_upload_review", upload_id=upload_id)
    await _permission(request, "v1_branch_create")
    async with _LOCK:
        record = uploads._record(request, env, upload_id)
        data = uploads._verified_bytes(record)
        if record.status != "reviewed" or record.version != payload.expected_version:
            raise ConflictError(
                "Save a valid, current column review before choosing a destination."
            )
        header, rows, errors = uploads._parse(data)
        _, count, _ = uploads._review_rows(header, rows, record.columns)
        if errors or count or not rows:
            raise ConflictError("The complete retained file must validate and contain rows.")
        existing = next((item for item in _all() if item.upload_id == upload_id), None)
        if existing:
            _, receipt = _receipt(request, env, upload_id)
            if receipt.table != payload.table:
                raise ConflictError(
                    "This upload already has an immutable destination. Upload again for a different destination."
                )
            if receipt.status != "preparing":
                return receipt
        else:
            if any(
                asset.id == f"dlt_{payload.table}"
                for asset in await v1_assets._assets(request, env)
            ):
                raise ConflictError(
                    "Destination asset identity already exists. Choose a new table."
                )
            base_hash = await _head(target.nessie_ref)
            await _new_relation(target.nessie_ref, payload.table)
            receipt = CandidateReceipt(
                upload_id=upload_id,
                env=env,
                actor=record.actor,
                review_version=record.version,
                checksum=record.checksum,
                recipe_digest=_recipe(record),
                table=payload.table,
                relation=f"bronze.{payload.table}",
                asset_id=f"dlt_{payload.table}",
                candidate_ref=f"{env}-upload-{upload_id}",
                base_ref=target.nessie_ref,
                base_hash=base_hash,
                candidate_hash=base_hash,
                status="preparing",
                repository_location=target.dagster_location,
            )
            _save(receipt, previous=None)
        binding = await provision_candidate(
            env=env,
            actor=record.actor,
            kind="upload",
            operation_id=upload_id,
            base_ref=receipt.base_ref,
            base_hash=receipt.base_hash,
        )
        if not receipt.candidate_binding:
            updated = receipt.model_copy(update={"candidate_binding": binding})
            _save(updated, previous=receipt)
            receipt = updated
        elif receipt.candidate_binding != binding:
            raise ConflictError("Managed candidate binding changed.")
        await verify_candidate(receipt.candidate_binding, expected_hash=receipt.candidate_hash)
        await _new_relation(receipt.candidate_ref, receipt.table)
        record_key = "upload_record_id"
        choices = [column.recipe() for column in record.columns]
        while record_key in {column["name"] for column in csv_output_columns(choices)}:
            record_key = f"{record_key}_key"
        manifest = {
            "parser_version": 1,
            "upload_id": upload_id,
            "checksum": record.checksum,
            "review_version": record.version,
            "recipe_digest": receipt.recipe_digest,
            "columns": choices,
            "row_count": record.row_count,
            "actor": record.actor,
            "env": env,
            "candidate_ref": receipt.candidate_ref,
            "table": receipt.table,
            "record_key": record_key,
        }
        result = await asyncio.to_thread(
            create_workflow_with_provider,
            project_root=project_root(),
            workflow_type="ingestion",
            domain="uploads",
            table=receipt.table,
            unique_key=record_key,
            cron="",
            provider="dlt",
            contribution_id="dlt.reviewed-csv-upload",
            values={"manifest": manifest},
        )
        await _reload(target.dagster_location)
        updated = receipt.model_copy(
            update={
                "status": "prepared",
                "files": result.files,
                "contract_digest": result.metadata["contract_digest"],
                "source_digest": result.metadata["source_digest"],
            }
        )
        _save(updated, previous=receipt)
        audit_operation(
            operation="csv_upload.destination",
            target=upload_id,
            dry_run=False,
            auth=auth,
            payload={"recipe_digest": receipt.recipe_digest},
            result=updated.model_dump(),
        )
        return updated


@router.get("/uploads/csv/{upload_id}/candidate", response_model=CandidateReceipt)
async def v1_csv_candidate(
    request: Request, upload_id: str, env: Environment = Query()
) -> CandidateReceipt:
    _target(request, env)
    async with _LOCK:
        _, receipt = _receipt(request, env, upload_id)
        if receipt.run_id and not receipt.loaded:
            status, _ = await _run(receipt)
            observed = "failed" if status in {"FAILURE", "CANCELED"} else "running"
            if observed != receipt.status:
                return _save(receipt.model_copy(update={"status": observed}), previous=receipt)
        return receipt


@router.post("/uploads/csv/{upload_id}/candidate/plan", response_model=CandidateReceipt)
async def v1_csv_candidate_plan(
    request: Request, upload_id: str, env: Environment = Query()
) -> CandidateReceipt:
    _target(request, env)
    uploads._upload_auth(request)
    require_scope(request, "lakehouse:operate")
    async with _LOCK:
        _, receipt = _receipt(request, env, upload_id)
        if receipt.status not in {"prepared", "planned", "failed", "queued", "running"}:
            raise ConflictError("Prepare the destination before estimating its one-off load.")
        await _permission(request, "v1_asset_materialize", asset_id=receipt.asset_id)
        await _assert_head(receipt)
        attempts = list(receipt.attempts)
        if receipt.run_id:
            status, _ = await _run(receipt)
            if status not in {"FAILURE", "CANCELED"} or receipt.loaded:
                raise ConflictError(
                    "Only an attested failed run with no candidate write can be replanned."
                )
            await _new_relation(receipt.candidate_ref, receipt.table)
            if len(attempts) >= 10:
                raise ConflictError("CSV candidate retry limit reached.")
            attempts.append(
                CandidateAttempt(
                    run_id=receipt.run_id,
                    status=status,
                    plan_hash=receipt.plan_hash or "",
                    job_snapshot_id=receipt.job_snapshot_id or "",
                    launch_ref_hash=receipt.launch_ref_hash or "",
                )
            )
        estimate = await v1_assets.v1_materialization_estimate(
            request,
            receipt.asset_id,
            env,
            partition_count=1,
            mode="latest",
            write_ref=receipt.candidate_ref,
        )
        if not estimate.plan_hash or not estimate.job_snapshot_id:
            raise BackendUnavailableError("Dagster did not return an immutable load plan.")
        _, repository, _ = await v1_assets._action_context(request, env, receipt.asset_id)
        updated = receipt.model_copy(
            update={
                "status": "planned",
                "plan_hash": estimate.plan_hash,
                "job_name": estimate.job_name,
                "job_snapshot_id": estimate.job_snapshot_id,
                "launch_ref_hash": estimate.ref_hash,
                "repository_name": repository,
                "run_id": None,
                "attempts": attempts,
            }
        )
        return _save(updated, previous=receipt)


@router.post("/uploads/csv/{upload_id}/candidate/load", response_model=CandidateReceipt)
async def v1_csv_candidate_load(
    request: Request, upload_id: str, payload: CandidateLoad, env: Environment = Query()
) -> CandidateReceipt:
    _target(request, env)
    auth = uploads._upload_auth(request)
    require_scope(request, "lakehouse:operate")
    async with _LOCK:
        _, receipt = _receipt(request, env, upload_id)
        await _permission(request, "v1_asset_materialize", asset_id=receipt.asset_id)
        if (
            payload.expected_version != receipt.review_version
            or payload.recipe_digest != receipt.recipe_digest
            or payload.plan_hash != receipt.plan_hash
        ):
            raise ConflictError("Reviewed recipe or plan changed. Refresh before confirming.")
        expected_retry = receipt.attempts[-1].run_id if receipt.attempts else None
        if payload.retry_of_run_id != expected_retry:
            raise ConflictError("Confirm the exact failed run being retried.")
        if receipt.run_id:
            return receipt
        if receipt.status != "planned":
            raise ConflictError("Estimate the candidate load before confirming.")
        _require_load_runtime()
        await _assert_head(receipt)
        result = await v1_assets.v1_asset_materialize(
            request,
            receipt.asset_id,
            v1_assets.MaterializeAssetAction(
                mode="latest",
                write_ref=receipt.candidate_ref,
                plan_hash=receipt.plan_hash,
                confirmed=True,
                dry_run=False,
                idempotency_key=f"csv-load-{upload_id}-{receipt.recipe_digest[:16]}-{len(receipt.attempts)}",
            ),
            env,
        )
        run_ids = result.result.get("run_ids", [])
        if result.result.get("accepted") is not True or len(run_ids) != 1:
            raise BackendUnavailableError("Dagster did not acknowledge exactly one candidate run.")
        updated = receipt.model_copy(update={"status": "queued", "run_id": run_ids[0]})
        _save(updated, previous=receipt)
        audit_operation(
            operation="csv_upload.load",
            target=upload_id,
            dry_run=False,
            auth=auth,
            payload={"plan_hash": receipt.plan_hash, "retry_of_run_id": payload.retry_of_run_id},
            result={"run_id": updated.run_id},
        )
        return updated


async def _run(receipt: CandidateReceipt) -> tuple[str, str | None]:
    value = await graphql_request(
        resolve_dagster_url(),
        """query CsvCandidateRun($runId:ID!){runOrError(runId:$runId){__typename ... on Run{runId status pipelineName pipelineSnapshotId parentPipelineSnapshotId assetSelection{path} tags{key value} repositoryOrigin{repositoryName repositoryLocationName} assetMaterializations{runId assetKey{path} metadataEntries{label ... on TextMetadataEntry{text} ... on IntMetadataEntry{intRepr}}}}}}""",
        {"runId": receipt.run_id},
    )
    run = value.get("data", {}).get("runOrError", {})
    tags = {item.get("key"): item.get("value") for item in run.get("tags", [])}
    origin = run.get("repositoryOrigin") or {}
    if (
        run.get("__typename") != "Run"
        or run.get("runId") != receipt.run_id
        or run.get("pipelineName") != receipt.job_name
        or not run.get("pipelineSnapshotId")
        or receipt.job_snapshot_id
        not in {run.get("pipelineSnapshotId"), run.get("parentPipelineSnapshotId")}
        or [item.get("path") for item in run.get("assetSelection") or []] != [[receipt.asset_id]]
        or origin.get("repositoryName") != receipt.repository_name
        or origin.get("repositoryLocationName") != receipt.repository_location
        or tags.get("environment") != receipt.env
        or tags.get("phlo/ref") != receipt.candidate_ref
        or tags.get("phlo/plan") != receipt.plan_hash
        or tags.get("phlo/job_snapshot_id") != receipt.job_snapshot_id
        or tags.get("phlo/ref_hash") != receipt.launch_ref_hash
        or tags.get("phlo/actor") != receipt.actor
    ):
        raise ConflictError("Dagster run identity differs from the confirmed CSV candidate plan.")
    status = run.get("status", "UNKNOWN")
    if status != "SUCCESS":
        return status, None
    snapshots = []
    for event in run.get("assetMaterializations", []):
        if event.get("runId") != receipt.run_id or event.get("assetKey", {}).get("path") != [
            receipt.asset_id
        ]:
            continue
        metadata = {
            entry["label"]: entry.get("text", entry.get("intRepr"))
            for entry in event.get("metadataEntries", [])
        }
        if (
            metadata.get("table_name") == receipt.relation
            and metadata.get("write_branch") == receipt.candidate_ref
            and metadata.get("snapshot_id")
        ):
            snapshots.append(str(metadata["snapshot_id"]))
    if len(snapshots) != 1:
        raise ConflictError("The exact run did not attest one candidate table snapshot.")
    return status, snapshots[0]


class CsvCandidatePreview(WireModel):
    receipt: CandidateReceipt
    columns: list[str]
    column_types: list[str]
    rows: list[dict[str, Any]]
    has_more: bool
    row_count: int
    snapshot_id: str
    ref_hash: str
    candidate_ref: str
    relation: str
    query_id: str | None = None


@router.get("/uploads/csv/{upload_id}/candidate/preview", response_model=CsvCandidatePreview)
async def v1_csv_candidate_preview(
    request: Request, upload_id: str, env: Environment = Query()
) -> CsvCandidatePreview:
    _target(request, env)
    async with _LOCK:
        record, receipt = _receipt(request, env, upload_id)
        await _permission(request, "v1_asset_preview", asset_id=receipt.asset_id)
        if not receipt.run_id:
            raise ConflictError("No candidate load has been launched.")
        status, run_snapshot = await _run(receipt)
        if status != "SUCCESS":
            next_status = "failed" if status in {"FAILURE", "CANCELED"} else "running"
            if receipt.status != next_status:
                _save(receipt.model_copy(update={"status": next_status}), previous=receipt)
            raise ConflictError(
                f"Candidate run is {status.lower()}; no loaded result is available."
            )
        preview = await preview_candidate(
            receipt.candidate_binding,
            receipt.relation,
            expected_hash=receipt.candidate_hash if receipt.loaded else None,
            expected_snapshot_id=receipt.snapshot_id or run_snapshot,
            limit=25,
        )
        if preview["row_count"] != record.row_count:
            raise ConflictError("Observed candidate table row count differs from the reviewed CSV.")
        if not receipt.loaded:
            updated = receipt.model_copy(
                update={
                    "status": "loaded",
                    "loaded": True,
                    "candidate_hash": preview["ref_hash"],
                    "snapshot_id": str(preview["snapshot_id"]),
                    "row_count": preview["row_count"],
                    "main_unchanged": await _head(receipt.base_ref) == receipt.base_hash,
                }
            )
            _save(updated, previous=receipt)
            receipt = updated
        fields = {
            key: preview.get(key) for key in CsvCandidatePreview.model_fields if key != "receipt"
        }
        fields["snapshot_id"] = str(preview["snapshot_id"])
        return CsvCandidatePreview.model_validate({"receipt": receipt, **fields})
