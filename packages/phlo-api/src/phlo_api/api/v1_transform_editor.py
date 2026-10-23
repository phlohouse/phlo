"""Authorized access to provider-owned immutable SQL drafts and read-only plans."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import uuid4
from importlib import import_module
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import Field, StringConstraints
from sqlglot import exp, parse_one
from sqlglot.optimizer.scope import traverse_scope

from phlo_api.api.authentication import get_request_principal
from phlo_api.api.operation_controls import (
    MutationRejected,
    audit_operation,
    project_root,
    replay_or_execute_async,
    require_scope,
)
from phlo_api.api.v1 import _target
from phlo_api.api.v1_assets import _assets, v1_transform
from phlo.rbac.models import CanonicalAction
from phlo_api.api.v1_query_sql import InvalidWorkspaceQuery, validate_workspace_query
from phlo_api.observatory_api.v1_preview import (
    PreviewUnavailable,
    preview_catalog,
    resolve_trino_url,
)
from phlo_api.errors import BackendUnavailableError, NotFoundError
from phlo_api.security_manifest import HTTP_ROUTE_MANIFEST, enforce_http_operation
from phlo_api.settings import get_process_settings
from phlo_api.v1_contract import Environment, WireModel

router = APIRouter(tags=["v1 transform editor"])
ModelId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_.]{0,255}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
DraftId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9-]{36}$")]


class TransformSource(WireModel):
    env: Environment
    model: str
    path: str
    sql: str
    source_digest: Digest
    workspace_digest: Digest
    model_version: str


class TransformDraftWrite(WireModel):
    sql: str = Field(min_length=1, max_length=256 * 1024)
    source_digest: Digest
    workspace_digest: Digest
    draft_id: DraftId | None = None
    expected_revision: int = Field(default=0, ge=0)


class TransformDraft(WireModel):
    env: Environment
    ref: str
    model: str
    path: str
    sql: str
    draft_id: str
    revision: int
    source_digest: Digest
    workspace_digest: Digest
    revision_digest: Digest
    sql_digest: Digest
    created_at: str


class TransformCompilation(WireModel):
    env: Environment
    draft_id: str
    revision: int
    revision_digest: Digest
    ref: str
    status: Literal["ready", "failed"]
    diagnostics: list[dict[str, Any]]
    plan_id: str | None
    compiled_sql: str | None
    target: str | None
    sql_hash: str | None
    desired_version: str | None
    materialization_available: Literal[False] = False
    source: Literal["phlo-transform-engine"] = "phlo-transform-engine"


def _provider() -> Any:
    try:
        return import_module("phlo_transform.editor")
    except ImportError as exc:
        raise BackendUnavailableError(
            "The native Phlo Transform editor provider is not installed."
        ) from exc


async def _call(method: str, *args: Any, **kwargs: Any) -> Any:
    provider = (
        import_module("phlo_transform.editor_execution")
        if method
        in {
            "prepare_model_execution",
            "execute_model_draft",
            "read_model_execution",
            "list_model_executions",
            "record_execution_preview",
            "record_execution_observation",
        }
        else _provider()
    )
    try:
        return await asyncio.to_thread(getattr(provider, method), *args, **kwargs)
    except provider.TransformEditorError as exc:
        raise HTTPException(
            status_code=exc.status, detail={"error": exc.reason, "message": str(exc)}
        ) from exc


async def _model_scope(request: Request, env: Environment, model: str) -> tuple[str, str]:
    target = _target(request, env)
    workspace = await v1_transform(request, env)
    selected = next((item for item in workspace.models if item.model == model), None)
    if selected is None:
        raise NotFoundError("The transform model is not declared in this environment.")
    await enforce_http_operation(
        request, HTTP_ROUTE_MANIFEST["v1_asset_detail"], {"asset_id": selected.asset_id}
    )
    principal = get_request_principal(request)
    if principal is None:
        raise HTTPException(401, "Authentication is required.")
    return principal.subject, target.nessie_ref


def _draft_view(payload: dict[str, Any]) -> TransformDraft:
    return TransformDraft.model_validate({key: payload[key] for key in TransformDraft.model_fields})


@router.get("/transform/models/{model_id}/source", response_model=TransformSource)
async def v1_transform_model_source(
    request: Request, model_id: ModelId, env: Environment = Query()
) -> TransformSource:
    await _model_scope(request, env, model_id)
    payload = await _call("read_model_source", project_root(), model_id)
    return TransformSource(env=env, **payload)


@router.post("/transform/models/{model_id}/drafts", response_model=TransformDraft, status_code=201)
async def v1_transform_model_draft_save(
    request: Request, model_id: ModelId, payload: TransformDraftWrite, env: Environment = Query()
) -> TransformDraft:
    auth = require_scope(request, "project:write")
    actor, ref = await _model_scope(request, env, model_id)
    draft = await _call(
        "save_model_draft",
        project_root(),
        model_id,
        actor=actor,
        env=env,
        ref=ref,
        **payload.model_dump(),
    )
    audit_operation(
        operation="transform.draft.save",
        target=f"{env}:{model_id}:{draft['draft_id']}",
        dry_run=False,
        auth=auth,
        payload={"expected_revision": payload.expected_revision, "sql_digest": draft["sql_digest"]},
        result={"revision": draft["revision"], "revision_digest": draft["revision_digest"]},
    )
    return _draft_view(draft)


@router.get(
    "/transform/models/{model_id}/drafts/{draft_id}/{revision}", response_model=TransformDraft
)
async def v1_transform_model_draft(
    request: Request,
    model_id: ModelId,
    draft_id: DraftId,
    revision: Annotated[int, Path(ge=1)],
    env: Environment = Query(),
) -> TransformDraft:
    actor, ref = await _model_scope(request, env, model_id)
    return _draft_view(
        await _call(
            "read_model_draft",
            project_root(),
            model_id,
            draft_id=draft_id,
            revision=revision,
            actor=actor,
            env=env,
            ref=ref,
        )
    )


async def _visible_sources(request: Request, env: Environment) -> set[str]:
    visible: set[str] = set()
    for asset in await _assets(request, env):
        if not asset.relation:
            continue
        try:
            await enforce_http_operation(
                request, HTTP_ROUTE_MANIFEST["v1_asset_detail"], {"asset_id": asset.id}
            )
        except HTTPException as exc:
            if exc.status_code == 403:
                continue
            raise
        visible.add(asset.relation)
    return visible


def _validate_draft_sources(sql: str, catalog: str, allowed_sources: set[str]) -> None:
    try:
        validated = validate_workspace_query(sql, catalog)
    except InvalidWorkspaceQuery as exc:
        raise HTTPException(422, detail={"error": "invalid_query", "message": str(exc)}) from exc
    # Scope resolution distinguishes a CTE name from the actual tables it reads.
    for scope in traverse_scope(parse_one(validated, read="trino")):
        for _, source in scope.sources.items():
            if isinstance(source, exp.Table):
                relation = f"{source.db}.{source.name}"
                if not source.db or relation not in allowed_sources:
                    raise HTTPException(
                        403, "Every SQL source must be an authorized environment asset."
                    )


@router.post(
    "/transform/models/{model_id}/drafts/{draft_id}/{revision}/compile",
    response_model=TransformCompilation,
)
async def v1_transform_model_compile(
    request: Request,
    model_id: ModelId,
    draft_id: DraftId,
    revision: Annotated[int, Path(ge=1)],
    env: Environment = Query(),
) -> TransformCompilation:
    actor, ref = await _model_scope(request, env, model_id)
    await enforce_http_operation(
        request, HTTP_ROUTE_MANIFEST["v1_transform_model_source"], {"model_id": model_id}
    )
    try:
        catalog = preview_catalog(env, ref)
        endpoint = resolve_trino_url()
    except PreviewUnavailable as exc:
        raise BackendUnavailableError(str(exc)) from exc
    visible_sources = await _visible_sources(request, env)
    draft = await _call(
        "read_model_draft",
        project_root(),
        model_id,
        draft_id=draft_id,
        revision=revision,
        actor=actor,
        env=env,
        ref=ref,
    )
    _validate_draft_sources(draft["sql"], catalog, visible_sources)
    compiled = await _call(
        "compile_model_draft",
        project_root(),
        model_id,
        allowed_sources=visible_sources,
        required_ref=ref,
        required_catalog=catalog,
        required_endpoint=endpoint,
        required_user=f"phlo_api_preview_{env}",
        trusted_ca_file=get_process_settings().phlo_v1_preview_trino_ca_file,
        draft_id=draft_id,
        revision=revision,
        actor=actor,
        env=env,
        ref=ref,
    )
    return TransformCompilation(env=env, **compiled)


class TransformExecuteWrite(WireModel):
    revision_digest: Digest
    base_hash: Annotated[str, StringConstraints(pattern=r"^[a-fA-F0-9]{8,128}$")]


class TransformExecution(WireModel):
    execution_id: str
    model: str
    env: Environment
    ref: str
    draft_id: str
    revision: int
    revision_digest: Digest
    status: Literal["prepared", "running", "passed", "failed", "unknown"]
    created_at: str
    candidate_ref: str | None
    candidate_catalog: str | None
    candidate_binding: dict[str, Any] | None = None
    candidate_result_hash: str | None = None
    candidate_snapshot_id: str | None = None
    report: dict[str, Any] | None
    preview: dict[str, Any] | None = None


class TransformExecutionPage(WireModel):
    env: Environment
    items: list[TransformExecution]


def _execution_view(record: dict[str, Any]) -> TransformExecution:
    return TransformExecution.model_validate(
        {key: record[key] for key in TransformExecution.model_fields if key in record}
    )


def _runtime() -> Any:
    try:
        return import_module("phlo_api.candidate_runtime")
    except ImportError as exc:
        raise BackendUnavailableError("Verified candidate execution is not installed.") from exc


async def _execution_identity(
    request: Request, env: Environment, model: str, draft_id: str, revision: int
) -> dict[str, Any]:
    actor, ref = await _model_scope(request, env, model)
    return {"actor": actor, "env": env, "ref": ref, "draft_id": draft_id, "revision": revision}


async def _execution_sources(
    request: Request, env: Environment, model: str, identity: dict[str, Any]
) -> dict[str, Any]:
    draft = await _call("read_model_draft", project_root(), model, **identity)
    catalog = preview_catalog(env, identity["ref"])
    _validate_draft_sources(draft["sql"], catalog, await _visible_sources(request, env))
    # Explicit physical catalog names cannot be retargeted into a fresh branch.
    statement = parse_one(draft["sql"], read="trino")
    if any(table.catalog for table in statement.find_all(exp.Table)):
        raise HTTPException(
            422, "Candidate execution requires schema-qualified sources without explicit catalogs."
        )
    return draft


@router.post(
    "/transform/models/{model_id}/drafts/{draft_id}/{revision}/executions",
    response_model=TransformExecution,
    status_code=201,
)
async def v1_transform_model_execute(
    request: Request,
    model_id: ModelId,
    draft_id: DraftId,
    revision: Annotated[int, Path(ge=1)],
    payload: TransformExecuteWrite,
    env: Environment = Query(),
) -> TransformExecution:
    auth = require_scope(request, "project:write")
    require_scope(request, "lakehouse:operate")
    identity = await _execution_identity(request, env, model_id, draft_id, revision)
    await enforce_http_operation(
        request, HTTP_ROUTE_MANIFEST["v1_transform_model_draft_save"], {"model_id": model_id}
    )
    workspace = await v1_transform(request, env)
    selected = next(item for item in workspace.models if item.model == model_id)
    await enforce_http_operation(
        request,
        replace(HTTP_ROUTE_MANIFEST["v1_asset_detail"], action=CanonicalAction.ASSET_EXECUTE.value),
        {"asset_id": selected.asset_id},
    )
    draft = await _execution_sources(request, env, model_id, identity)
    if draft["revision_digest"] != payload.revision_digest:
        raise HTTPException(
            409, "The immutable draft revision differs from the requested execution intent."
        )
    key = request.headers.get("idempotency-key", "").strip()
    if not 1 <= len(key) <= 200:
        raise HTTPException(400, "An idempotency key is required for candidate execution.")
    target = f"{identity['actor']}:{env}:{model_id}:{draft_id}:{revision}:{payload.revision_digest}:{payload.base_hash}"
    runtime = _runtime()

    async def execute() -> dict[str, Any]:
        # Compilation is read-only. Its rejection cannot make a candidate write uncertain.
        try:
            compiled = await v1_transform_model_compile(request, model_id, draft_id, revision, env)
            if compiled.status != "ready":
                raise HTTPException(
                    422, "The saved revision must compile successfully before candidate execution."
                )
        except HTTPException as exc:
            raise MutationRejected(
                exc.status_code,
                exc.detail,
                headers={**(exc.headers or {}), "X-Phlo-Mutation-Outcome": "rejected-before-write"},
            ) from exc
        except Exception as exc:
            raise MutationRejected(
                503,
                "Native compilation prerequisites are unavailable.",
                headers={"X-Phlo-Mutation-Outcome": "rejected-before-write"},
            ) from exc
        execution_id = str(uuid4())
        await _call(
            "prepare_model_execution",
            project_root(),
            model_id,
            execution_id=execution_id,
            **identity,
        )
        binding = await runtime.provision_candidate(
            env=env,
            actor=identity["actor"],
            kind="transform",
            operation_id=execution_id.replace("-", ""),
            base_ref=identity["ref"],
            base_hash=payload.base_hash,
        )
        await runtime.verify_candidate(binding, expected_hash=payload.base_hash)
        result = await _call(
            "execute_model_draft",
            project_root(),
            model_id,
            execution_id=execution_id,
            candidate_ref=binding["candidate_ref"],
            candidate_catalog=binding["catalog"],
            connection=runtime.candidate_connection(binding),
            candidate_binding=binding,
            **identity,
        )
        if result["status"] == "passed":
            final_hash = await runtime.verify_candidate(binding, expected_hash=None)
            result = await _call(
                "record_execution_observation",
                project_root(),
                model_id,
                execution_id=execution_id,
                candidate_result_hash=final_hash,
                **identity,
            )
            model_result = next(
                item for item in result["report"]["models"] if item["model"] == model_id
            )
            try:
                preview = await runtime.preview_candidate(
                    binding, model_result["target"], expected_hash=final_hash, limit=25
                )
                result = await _call(
                    "record_execution_preview",
                    project_root(),
                    model_id,
                    execution_id=execution_id,
                    preview=preview,
                    **identity,
                )
            except Exception:
                # A readback failure must not erase or rerun a durable native success.
                pass
        return result

    result = await replay_or_execute_async(
        idempotency_key=key,
        operation="transform.candidate.execute",
        target=target,
        exclusion_target=f"transform:{identity['actor']}:{env}:{draft_id}:{revision}",
        execute=execute,
        audit=lambda outcome: audit_operation(
            operation="transform.candidate.execute",
            target=target,
            dry_run=False,
            auth=auth,
            payload={"revision_digest": payload.revision_digest, "base_hash": payload.base_hash},
            result={
                "execution_id": outcome["execution_id"],
                "status": outcome["status"],
                "candidate_ref": outcome["candidate_ref"],
                "native_run_id": (outcome.get("report") or {}).get("run_id"),
            },
        ),
    )
    return _execution_view(result)


@router.get(
    "/transform/models/{model_id}/drafts/{draft_id}/{revision}/executions",
    response_model=TransformExecutionPage,
)
async def v1_transform_model_executions(
    request: Request,
    model_id: ModelId,
    draft_id: DraftId,
    revision: Annotated[int, Path(ge=1)],
    env: Environment = Query(),
) -> TransformExecutionPage:
    identity = await _execution_identity(request, env, model_id, draft_id, revision)
    await _execution_sources(request, env, model_id, identity)
    items = await _call("list_model_executions", project_root(), model_id, **identity)
    views = []
    for item in items:
        if item.get("candidate_binding"):
            try:
                await _runtime().verify_candidate(
                    item["candidate_binding"], expected_hash=item.get("candidate_result_hash")
                )
            except Exception:
                # Retain immutable native run evidence when its live candidate
                # no longer verifies; do not expose unverified result rows.
                item = {**item, "preview": None}
        views.append(_execution_view(item))
    return TransformExecutionPage(env=env, items=views)


@router.get(
    "/transform/models/{model_id}/drafts/{draft_id}/{revision}/executions/{execution_id}",
    response_model=TransformExecution,
)
async def v1_transform_model_execution(
    request: Request,
    model_id: ModelId,
    draft_id: DraftId,
    revision: Annotated[int, Path(ge=1)],
    execution_id: DraftId,
    env: Environment = Query(),
) -> TransformExecution:
    identity = await _execution_identity(request, env, model_id, draft_id, revision)
    await _execution_sources(request, env, model_id, identity)
    result = await _call(
        "read_model_execution", project_root(), model_id, execution_id=execution_id, **identity
    )
    if result.get("candidate_binding"):
        await _runtime().verify_candidate(
            result["candidate_binding"], expected_hash=result.get("candidate_result_hash")
        )
    return _execution_view(result)


@router.get(
    "/transform/models/{model_id}/drafts/{draft_id}/{revision}/executions/{execution_id}/preview",
    response_model=TransformExecution,
)
async def v1_transform_model_execution_preview(
    request: Request,
    model_id: ModelId,
    draft_id: DraftId,
    revision: Annotated[int, Path(ge=1)],
    execution_id: DraftId,
    env: Environment = Query(),
) -> TransformExecution:
    identity = await _execution_identity(request, env, model_id, draft_id, revision)
    await _execution_sources(request, env, model_id, identity)
    result = await _call(
        "read_model_execution", project_root(), model_id, execution_id=execution_id, **identity
    )
    if result["status"] != "passed" or not result.get("candidate_result_hash"):
        raise HTTPException(409, "A verified successful candidate result is required for preview.")
    model_result = next(item for item in result["report"]["models"] if item["model"] == model_id)
    preview = await _runtime().preview_candidate(
        result["candidate_binding"],
        model_result["target"],
        expected_hash=result["candidate_result_hash"],
        expected_snapshot_id=result.get("candidate_snapshot_id"),
        limit=25,
    )
    return _execution_view(
        await _call(
            "record_execution_preview",
            project_root(),
            model_id,
            execution_id=execution_id,
            preview=preview,
            **identity,
        )
    )


class TransformDraftSummary(WireModel):
    draft_id: str
    revision: int
    model: str
    env: Environment
    ref: str
    path: str
    created_at: str
    source_digest: Digest
    revision_digest: Digest
    sql_digest: Digest


class TransformDraftPage(WireModel):
    env: Environment
    items: list[TransformDraftSummary]
    total: int
    truncated: bool


class TransformDraftComparison(WireModel):
    env: Environment
    draft_id: str
    revision: int
    revision_digest: Digest
    source_digest: Digest
    source_path: str
    base_kind: Literal["workspace_source"] = "workspace_source"
    baseline_matches_saved_source: bool
    added_lines: int
    removed_lines: int
    unified_diff: list[str]


@router.get("/transform/models/{model_id}/drafts", response_model=TransformDraftPage)
async def v1_transform_model_drafts(
    request: Request, model_id: ModelId, env: Environment = Query()
) -> TransformDraftPage:
    actor, ref = await _model_scope(request, env, model_id)
    payload = await _call(
        "list_model_drafts", project_root(), model_id, actor=actor, env=env, ref=ref
    )
    return TransformDraftPage(env=env, **payload)


@router.get(
    "/transform/models/{model_id}/drafts/{draft_id}/{revision}/compare",
    response_model=TransformDraftComparison,
)
async def v1_transform_model_draft_compare(
    request: Request,
    model_id: ModelId,
    draft_id: DraftId,
    revision: Annotated[int, Path(ge=1)],
    env: Environment = Query(),
) -> TransformDraftComparison:
    actor, ref = await _model_scope(request, env, model_id)
    payload = await _call(
        "compare_model_draft",
        project_root(),
        model_id,
        actor=actor,
        env=env,
        ref=ref,
        draft_id=draft_id,
        revision=revision,
    )
    return TransformDraftComparison(env=env, **payload)
