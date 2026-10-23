"""Authorized access to provider-owned immutable SQL drafts and read-only plans."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from uuid import uuid4
from urllib.parse import urlencode
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
    reclamation_guard,
    replay_or_execute_async,
    resolve_idempotency_claim,
    require_scope,
    validate_payload_retention,
)
from phlo_api.api.v1 import _target
from phlo_api.api.v1_assets import _assets, v1_transform
from phlo_api.candidate_runtime import CandidateEvidence, CandidateHandoff, CandidateReconciliation
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
    project_path: str | None = None


class TransformDraftWrite(WireModel):
    sql: str = Field(min_length=1, max_length=256 * 1024)
    source_digest: Digest
    workspace_digest: Digest
    draft_id: DraftId | None = None
    expected_revision: int = Field(default=0, ge=0)


class ReclaimedPayload(WireModel):
    reclaim_started_at: str | None = None
    reclaimed_at: str | None = None
    reclaimed_by: str | None = None
    reclaim_reason: str | None = None
    reclaimed_bytes: int = 0


class TransformDraft(ReclaimedPayload):
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
    archived_at: str | None = None
    archived_by: str | None = None
    archive_reason: str | None = None


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
            "archive_model_execution",
            "reclaim_model_execution",
            "reconcile_model_execution",
        }
        else _provider()
    )
    try:
        return await asyncio.to_thread(getattr(provider, method), *args, **kwargs)
    except provider.TransformEditorError as exc:
        raise HTTPException(
            status_code=exc.status, detail={"error": exc.reason, "message": str(exc)}
        ) from exc


async def _model_scope(
    request: Request,
    env: Environment,
    model: str,
    *,
    allowed_query: frozenset[str] = frozenset({"env"}),
) -> tuple[str, str]:
    target = _target(request, env, allowed_query=allowed_query)
    scoped = Request({**request.scope, "query_string": urlencode({"env": env}).encode()})
    workspace = await v1_transform(scoped, env)
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
    return TransformDraft.model_validate(
        {key: payload[key] for key in TransformDraft.model_fields if key in payload}
    )


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


class TransformExecution(ReclaimedPayload):
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
    evidence: CandidateEvidence | None = None
    reconciliation: CandidateReconciliation | None = None
    archived_at: str | None = None
    archived_by: str | None = None
    archive_reason: str | None = None


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
    request: Request,
    env: Environment,
    model: str,
    draft_id: str,
    revision: int,
    *,
    allowed_query: frozenset[str] = frozenset({"env"}),
) -> dict[str, Any]:
    actor, ref = await _model_scope(request, env, model, allowed_query=allowed_query)
    return {"actor": actor, "env": env, "ref": ref, "draft_id": draft_id, "revision": revision}


async def _execution_sources(
    request: Request,
    env: Environment,
    model: str,
    identity: dict[str, Any],
    *,
    require_active: bool = True,
) -> dict[str, Any]:
    draft = await _call("read_model_draft", project_root(), model, **identity)
    if require_active and draft.get("archived_at"):
        raise HTTPException(409, "Restore the archived revision before candidate operations.")
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
            operation_claim={"key": key, "target": target},
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
    include_archived: bool = False,
) -> TransformExecutionPage:
    identity = await _execution_identity(
        request,
        env,
        model_id,
        draft_id,
        revision,
        allowed_query=frozenset({"env", "include_archived"}),
    )
    draft = await _call("read_model_draft", project_root(), model_id, **identity)
    items = await _call(
        "list_model_executions",
        project_root(),
        model_id,
        include_archived=include_archived,
        **identity,
    )
    source_authorized = await _history_sources_authorized(request, env, model_id, identity)
    views = [
        await _historical_execution(
            {**item, "source_archived": bool(draft.get("archived_at"))},
            source_authorized=source_authorized,
            include_rows=True,
        )
        for item in items
    ]
    return TransformExecutionPage(env=env, items=views)


async def _history_sources_authorized(
    request: Request, env: Environment, model: str, identity: dict[str, Any]
) -> bool:
    try:
        scoped = Request({**request.scope, "query_string": urlencode({"env": env}).encode()})
        await _execution_sources(scoped, env, model, identity, require_active=False)
    except Exception:
        return False
    return True


async def _historical_execution(
    result: dict[str, Any], *, source_authorized: bool, include_rows: bool = False
) -> TransformExecution:
    evidence = await _runtime().candidate_evidence(
        result.get("candidate_binding") or {}, expected_hash=result.get("candidate_result_hash")
    )
    preview = None
    if not source_authorized:
        evidence.update(
            eligible_for_release=False, reason="Current source access or validation is unavailable."
        )
    elif not result.get("candidate_result_hash") or not result.get("candidate_snapshot_id"):
        evidence.update(
            eligible_for_release=False,
            reason="No pinned output hash and snapshot are recorded; reconcile the saved result.",
        )
    elif evidence["observed_candidate_hash"]:
        selected = [
            item
            for item in (result.get("report") or {}).get("models", [])
            if item.get("model") == result["model"]
        ]
        try:
            if len(selected) != 1:
                raise ValueError("A single recorded model target is required.")
            preview = await _runtime().preview_candidate(
                result["candidate_binding"],
                selected[0]["target"],
                expected_hash=result["candidate_result_hash"],
                expected_snapshot_id=result["candidate_snapshot_id"],
                require_current_base=False,
            )
        except Exception:
            evidence.update(
                stale=True,
                eligible_for_release=False,
                reason="The pinned candidate table and snapshot cannot be verified; retained report only.",
            )
    if result["status"] != "passed" or result.get("archived_at") or result.get("source_archived"):
        evidence.update(
            eligible_for_release=False, reason="The execution is not an active verified success."
        )
    return _execution_view(
        {**result, "evidence": evidence, "preview": preview if include_rows else None}
    )


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
    draft = await _call("read_model_draft", project_root(), model_id, **identity)
    result = await _call(
        "read_model_execution", project_root(), model_id, execution_id=execution_id, **identity
    )
    return await _historical_execution(
        {**result, "source_archived": bool(draft.get("archived_at"))},
        source_authorized=await _history_sources_authorized(request, env, model_id, identity),
        include_rows=True,
    )


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


class TransformDraftSummary(ReclaimedPayload):
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
    archived_at: str | None = None
    archived_by: str | None = None
    archive_reason: str | None = None


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
    request: Request, model_id: ModelId, env: Environment = Query(), include_archived: bool = False
) -> TransformDraftPage:
    actor, ref = await _model_scope(
        request, env, model_id, allowed_query=frozenset({"env", "include_archived"})
    )
    payload = await _call(
        "list_model_drafts",
        project_root(),
        model_id,
        actor=actor,
        env=env,
        ref=ref,
        include_archived=include_archived,
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


class TransformReclaim(WireModel):
    confirmed: Literal[True]
    expected_revision_digest: Digest
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")


@router.post(
    "/transform/models/{model_id}/drafts/{draft_id}/{revision}/reclaim",
    response_model=TransformDraft,
)
async def v1_transform_model_draft_reclaim(
    request: Request,
    model_id: ModelId,
    draft_id: DraftId,
    revision: Annotated[int, Path(ge=1)],
    payload: TransformReclaim,
    env: Environment = Query(),
) -> TransformDraft:
    auth = require_scope(request, "project:write")
    identity = await _execution_identity(request, env, model_id, draft_id, revision)
    executions = await _call(
        "list_model_executions", project_root(), model_id, include_archived=True, **identity
    )
    draft = await _call("read_model_draft", project_root(), model_id, **identity)
    validate_payload_retention(draft["created_at"])
    if draft.get("archived_at"):
        validate_payload_retention(draft["archived_at"])
    guards = [
        (
            "transform.candidate.execute",
            f"transform:{identity['actor']}:{env}:{draft_id}:{revision}",
        )
    ]
    guards.extend(
        ("transform.candidate.execute", item["operation_claim"]["target"])
        for item in executions
        if (item.get("operation_claim") or {}).get("target")
    )
    with reclamation_guard(guards):
        audit_operation(
            operation="transform.draft.reclaim_requested",
            target=f"{env}:{model_id}:{draft_id}:{revision}",
            dry_run=False,
            auth=auth,
            payload=payload.model_dump(),
            result={"evidence_retained": True},
        )
        result = await _call(
            "reclaim_model_draft",
            project_root(),
            model_id,
            expected_revision_digest=payload.expected_revision_digest,
            expected_record=draft,
            reason=payload.reason,
            **identity,
        )
    audit_operation(
        operation="transform.draft.reclaim",
        target=f"{env}:{model_id}:{draft_id}:{revision}",
        dry_run=False,
        auth=auth,
        payload=payload.model_dump(),
        result={"reclaimed_bytes": result["reclaimed_bytes"], "evidence_retained": True},
    )
    return _draft_view(result)


@router.post(
    "/transform/models/{model_id}/drafts/{draft_id}/{revision}/executions/{execution_id}/reclaim",
    response_model=TransformExecution,
)
async def v1_transform_model_execution_reclaim(
    request: Request,
    model_id: ModelId,
    draft_id: DraftId,
    revision: Annotated[int, Path(ge=1)],
    execution_id: DraftId,
    payload: TransformReclaim,
    env: Environment = Query(),
) -> TransformExecution:
    auth = require_scope(request, "project:write")
    identity = await _execution_identity(request, env, model_id, draft_id, revision)
    record = await _call(
        "read_model_execution", project_root(), model_id, execution_id=execution_id, **identity
    )
    validate_payload_retention(record["created_at"])
    # Native outputs may be newer than preparation. Archival follows terminal execution.
    if record.get("archived_at"):
        validate_payload_retention(record["archived_at"])
    guards = [
        (
            "transform.candidate.execute",
            f"transform:{identity['actor']}:{env}:{draft_id}:{revision}",
        )
    ]
    if (record.get("operation_claim") or {}).get("target"):
        guards.append(("transform.candidate.execute", record["operation_claim"]["target"]))
    with reclamation_guard(guards):
        audit_operation(
            operation="transform.execution.reclaim_requested",
            target=f"{env}:{execution_id}",
            dry_run=False,
            auth=auth,
            payload=payload.model_dump(),
            result={"evidence_retained": True},
        )
        result = await _call(
            "reclaim_model_execution",
            project_root(),
            model_id,
            execution_id=execution_id,
            expected_revision_digest=payload.expected_revision_digest,
            expected_record=record,
            reason=payload.reason,
            **identity,
        )
    audit_operation(
        operation="transform.execution.reclaim",
        target=f"{env}:{execution_id}",
        dry_run=False,
        auth=auth,
        payload=payload.model_dump(),
        result={"reclaimed_bytes": result["reclaimed_bytes"], "evidence_retained": True},
    )
    return await _historical_execution(result, source_authorized=False)


class TransformArchive(WireModel):
    archived: bool
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")


@router.post(
    "/transform/models/{model_id}/drafts/{draft_id}/{revision}/archive",
    response_model=TransformDraft,
)
async def v1_transform_model_draft_archive(
    request: Request,
    model_id: ModelId,
    draft_id: DraftId,
    revision: Annotated[int, Path(ge=1)],
    payload: TransformArchive,
    env: Environment = Query(),
) -> TransformDraft:
    auth = require_scope(request, "project:write")
    identity = await _execution_identity(request, env, model_id, draft_id, revision)
    result = await _call(
        "archive_model_draft", project_root(), model_id, **payload.model_dump(), **identity
    )
    audit_operation(
        operation="transform.draft.archive" if payload.archived else "transform.draft.restore",
        target=f"{env}:{model_id}:{draft_id}:{revision}",
        dry_run=False,
        auth=auth,
        payload={"reason": payload.reason},
        result={
            "revision_digest": result["revision_digest"],
            "archived_at": result.get("archived_at"),
        },
    )
    return _draft_view(result)


@router.post(
    "/transform/models/{model_id}/drafts/{draft_id}/{revision}/executions/{execution_id}/archive",
    response_model=TransformExecution,
)
async def v1_transform_model_execution_archive(
    request: Request,
    model_id: ModelId,
    draft_id: DraftId,
    revision: Annotated[int, Path(ge=1)],
    execution_id: DraftId,
    payload: TransformArchive,
    env: Environment = Query(),
) -> TransformExecution:
    auth = require_scope(request, "project:write")
    identity = await _execution_identity(request, env, model_id, draft_id, revision)
    draft = await _call("read_model_draft", project_root(), model_id, **identity)
    result = await _call(
        "archive_model_execution",
        project_root(),
        model_id,
        execution_id=execution_id,
        **payload.model_dump(),
        **identity,
    )
    audit_operation(
        operation="transform.execution.archive"
        if payload.archived
        else "transform.execution.restore",
        target=f"{env}:{execution_id}",
        dry_run=False,
        auth=auth,
        payload={"reason": payload.reason},
        result={"status": result["status"], "evidence_retained": True},
    )
    return await _historical_execution(
        {**result, "source_archived": bool(draft.get("archived_at"))},
        source_authorized=await _history_sources_authorized(request, env, model_id, identity),
    )


@router.post(
    "/transform/models/{model_id}/drafts/{draft_id}/{revision}/executions/{execution_id}/reconcile",
    response_model=TransformExecution,
)
async def v1_transform_model_execution_reconcile(
    request: Request,
    model_id: ModelId,
    draft_id: DraftId,
    revision: Annotated[int, Path(ge=1)],
    execution_id: DraftId,
    env: Environment = Query(),
) -> TransformExecution:
    auth = require_scope(request, "project:write")
    require_scope(request, "lakehouse:operate")
    identity = await _execution_identity(request, env, model_id, draft_id, revision)
    draft = await _execution_sources(request, env, model_id, identity, require_active=False)
    result = await _call(
        "read_model_execution", project_root(), model_id, execution_id=execution_id, **identity
    )
    resolution = {
        "resolution": "unresolved",
        "reason": "No durable success report and output hash exist; the operation stays fenced. Never rerun an unknown mutation.",
    }
    if result.get("candidate_result_hash") and (result.get("report") or {}).get("models"):
        target = result["report"]["models"][0].get("target", "")
        try:
            preview = await _runtime().preview_candidate(
                result["candidate_binding"],
                target,
                expected_hash=result["candidate_result_hash"],
                expected_snapshot_id=result.get("candidate_snapshot_id"),
                require_current_base=False,
            )
            result = await _call(
                "reconcile_model_execution",
                project_root(),
                model_id,
                execution_id=execution_id,
                preview=preview,
                **identity,
            )
        except Exception:
            resolution["reason"] = (
                "Recorded native success and provider snapshot do not agree or are unavailable; the operation stays fenced."
            )
        else:
            resolution = {"resolution": "verified_success", "reason": None}
            claim = result.get("operation_claim")
            if claim:
                try:
                    await asyncio.to_thread(
                        resolve_idempotency_claim,
                        idempotency_key=claim["key"],
                        operation="transform.candidate.execute",
                        target=claim["target"],
                        resolution="succeeded",
                        resolved_by=identity["actor"],
                        evidence={
                            "execution_id": execution_id,
                            "revision_digest": result["revision_digest"],
                            "ref_hash": preview["ref_hash"],
                            "snapshot_id": str(preview["snapshot_id"]),
                        },
                        response=result,
                    )
                except ValueError:
                    resolution["reason"] = (
                        "Execution verified. The operation claim is already complete or is not resolvable; no retry was enabled."
                    )
    audit_operation(
        operation="transform.execution.reconcile",
        target=f"{env}:{execution_id}",
        dry_run=False,
        auth=auth,
        payload={},
        result=resolution,
    )
    return await _historical_execution(
        {**result, "reconciliation": resolution, "source_archived": bool(draft.get("archived_at"))},
        source_authorized=True,
        include_rows=True,
    )


@router.get(
    "/transform/models/{model_id}/drafts/{draft_id}/{revision}/handoff",
    response_model=CandidateHandoff,
)
async def v1_transform_model_draft_handoff(
    request: Request,
    model_id: ModelId,
    draft_id: DraftId,
    revision: Annotated[int, Path(ge=1)],
    env: Environment = Query(),
    execution_id: DraftId | None = None,
) -> dict[str, Any]:
    identity = await _execution_identity(
        request, env, model_id, draft_id, revision, allowed_query=frozenset({"env", "execution_id"})
    )
    draft = await _call("read_model_draft", project_root(), model_id, **identity)
    evidence = None
    source_ref = None
    project_path = draft.get("project_path")
    if not project_path:
        source = await _call("read_model_source", project_root(), model_id)
        if source["path"] != draft["path"]:
            raise HTTPException(
                409, "The model source path moved; review the retained SQL separately."
            )
        project_path = source.get("project_path") or draft["path"]
    if execution_id:
        result = await _call(
            "read_model_execution", project_root(), model_id, execution_id=execution_id, **identity
        )
        view = await _historical_execution(
            result,
            source_authorized=await _history_sources_authorized(request, env, model_id, identity),
        )
        evidence = view.evidence.model_dump() if view.evidence is not None else None
        source_ref = view.candidate_ref
    if draft.get("archived_at") and evidence:
        evidence.update(eligible_for_release=False, reason="The source revision is archived.")
    return {
        "env": env,
        "kind": "transform_revision",
        "source_review_required": True,
        "automatic_promotion": False,
        "files": [
            {
                "path": project_path,
                "content": draft["sql"],
                "sha256": hashlib.sha256(draft["sql"].encode("utf-8")).hexdigest(),
            }
        ],
        "candidate_evidence": evidence,
        "release": _runtime().release_handoff(
            env=env, source_ref=source_ref, target_ref=identity["ref"], evidence=evidence
        ),
    }
