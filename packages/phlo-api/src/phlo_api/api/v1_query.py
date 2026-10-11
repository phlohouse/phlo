"""Ref-aware, bounded SQL workspace resources for ``/api/v1``."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field

from phlo.config.process import get_process_settings
from phlo_api.api.authentication import get_request_principal
from phlo_api.api.operation_controls import audit_operation, project_root, replay_or_execute
from phlo_api.api.v1 import _target
from phlo_api.api.v1_query_sql import (
    InvalidWorkspaceQuery,
    bound_workspace_query,
    validate_workspace_query,
)
from phlo_api.observatory_api.observatory_durable_state import load_collection, mutate_collection
from phlo_api.observatory_api.observatory_metadata import safe_metadata
from phlo_api.observatory_api.v1_preview import (
    PreviewLimitExceeded,
    PreviewQueryRejected,
    PreviewUnavailable,
    execute_preview,
    preview_catalog,
)
from phlo_api.errors import BackendUnavailableError
from phlo_api.settings import get_deployment_settings
from phlo_api.v1_contract import Environment, WireModel

router = APIRouter(tags=["v1 query workspace"])
_QUERY_COLLECTION = "v1_saved_queries"
_QUERY_LIMIT = 100
_QUERY_SESSION_LIMIT = 500


class CatalogSchema(WireModel):
    name: str
    tables: list[str]


class CatalogItem(WireModel):
    name: str
    schemas: list[CatalogSchema]
    truncated: bool


class QueryCatalog(WireModel):
    env: Environment
    nessie_ref: str
    engine: str
    catalogs: list[CatalogItem]


class QueryRef(WireModel):
    env: Environment
    name: str
    catalog: str


class QueryRefs(WireModel):
    env: Environment
    items: list[QueryRef]


class QueryEngine(WireModel):
    id: str
    status: Literal["configured", "unavailable"]


class QueryEngines(WireModel):
    env: Environment
    items: list[QueryEngine]


class QueryRequest(WireModel):
    sql: str = Field(min_length=1, max_length=64 * 1024)
    row_limit: int = Field(default=100, ge=1, le=100)
    engine: Literal["trino"] = "trino"


class QueryColumn(WireModel):
    name: str
    type: str | None


class QueryResult(WireModel):
    columns: list[QueryColumn]
    rows: list[dict[str, Any]]
    has_more: bool


class QuerySessionView(WireModel):
    id: str
    env: Environment
    nessie_ref: str
    engine: Literal["trino"] = "trino"
    evidence_available: bool = False
    status: Literal["queued", "running", "cancelling", "completed", "failed", "cancelled"]
    sql_hash: str
    created_at: datetime
    updated_at: datetime
    result: QueryResult | None
    error: str | None


class SavedQuery(WireModel):
    id: str
    env: Environment
    nessie_ref: str
    name: str = Field(min_length=1, max_length=120)
    sql: str = Field(min_length=1, max_length=64 * 1024)
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]


class SavedQueryRequest(WireModel):
    env: Environment
    name: str = Field(min_length=1, max_length=120)
    sql: str = Field(min_length=1, max_length=64 * 1024)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SavedQueryUpdate(WireModel):
    env: Environment
    expected_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)
    sql: str = Field(min_length=1, max_length=64 * 1024)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SavedQueryDelete(WireModel):
    env: Environment
    expected_version: int = Field(ge=1)


class SavedQueryPage(WireModel):
    env: Environment
    items: list[SavedQuery]


class QuerySession:
    def __init__(
        self,
        *,
        query_id: str,
        actor: str,
        env: Environment,
        nessie_ref: str,
        sql: str,
        catalog: str,
        row_limit: int,
        explain: bool = False,
    ) -> None:
        self.id = query_id
        self.actor = actor
        self.env = env
        self.nessie_ref = nessie_ref
        self.sql = sql
        self.catalog = catalog
        self.row_limit = row_limit
        self.explain = explain
        self.status: Literal[
            "queued", "running", "cancelling", "completed", "failed", "cancelled"
        ] = "queued"
        self.created_at = datetime.now(UTC)
        self.updated_at = self.created_at
        self.result: dict[str, Any] | None = None
        self.error: str | None = None
        self.cancel_requested = False
        self.trino_query_id: str | None = None
        self.active_uri: str | None = None
        self.task: asyncio.Task[None] | None = None
        self.evidence_available = False


_QUERY_SESSIONS: dict[str, QuerySession] = {}


def _actor(request: Request) -> str:
    principal = get_request_principal(request)
    return principal.subject if principal is not None else "development:anonymous"


def _prune_sessions() -> None:
    cutoff = datetime.now(UTC).timestamp() - 3600
    for query_id, session in tuple(_QUERY_SESSIONS.items()):
        if (
            session.status in {"completed", "failed", "cancelled"}
            and session.updated_at.timestamp() < cutoff
        ):
            del _QUERY_SESSIONS[query_id]


def _remember_session(session: QuerySession) -> None:
    _prune_sessions()
    if len(_QUERY_SESSIONS) >= _QUERY_SESSION_LIMIT:
        raise HTTPException(status_code=429, detail={"error": "query_session_limit_reached"})
    _QUERY_SESSIONS[session.id] = session
    session.task = asyncio.create_task(_execute_session(session))


def _mapped_catalog(request: Request, env: Environment) -> tuple[str, str]:
    target = _target(request, env)
    if not get_deployment_settings().query_single_replica:
        raise BackendUnavailableError(
            "Query workspace requires an explicitly single-replica API deployment."
        )
    try:
        catalog = preview_catalog(env, target.nessie_ref)
    except PreviewUnavailable as exc:
        raise BackendUnavailableError(str(exc)) from exc
    return catalog, target.nessie_ref


def _query_view(session: QuerySession) -> QuerySessionView:
    return QuerySessionView(
        id=session.id,
        env=session.env,
        nessie_ref=session.nessie_ref,
        evidence_available=session.evidence_available,
        status=session.status,
        sql_hash=hashlib.sha256(session.sql.encode()).hexdigest(),
        created_at=session.created_at,
        updated_at=session.updated_at,
        result=QueryResult.model_validate(session.result) if session.result is not None else None,
        error=session.error,
    )


def _audit_query(
    request: Request,
    *,
    operation: str,
    target: str,
    sql: str,
    outcome: str,
) -> None:
    auth = {"subject": _actor(request), "scopes": []}
    audit_operation(
        operation=operation,
        target=target,
        dry_run=False,
        auth=auth,
        payload={"sql_sha256": hashlib.sha256(sql.encode()).hexdigest()},
        result={"outcome": outcome},
    )


def _prepare_query_attempt(
    request: Request, env: Environment, *, operation: str, sql: str
) -> tuple[str, str]:
    try:
        catalog, nessie_ref = _mapped_catalog(request, env)
    except Exception:
        _audit_query(
            request,
            operation=operation,
            target=f"{env}:unavailable",
            sql=sql,
            outcome="unavailable",
        )
        raise
    _audit_query(
        request,
        operation=operation,
        target=f"{env}:{nessie_ref}",
        sql=sql,
        outcome="received",
    )
    return catalog, nessie_ref


def start_exact_asset_count(
    request: Request,
    env: Environment,
    *,
    table_name: str,
    snapshot_id: str,
) -> QuerySessionView:
    """Start a one-row count pinned to a server-selected table snapshot."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", table_name):
        raise HTTPException(status_code=400, detail={"error": "invalid_asset_relation"})
    if not re.fullmatch(r"[0-9]{1,19}", snapshot_id):
        raise HTTPException(status_code=503, detail={"error": "snapshot_identity_unavailable"})
    catalog, nessie_ref = _prepare_query_attempt(
        request,
        env,
        operation="asset.exact_row_count",
        sql=f"COUNT(*) on {table_name} snapshot {snapshot_id}",
    )
    schema, table = table_name.split(".")
    sql = (
        "SELECT CAST(COUNT(*) AS VARCHAR) AS row_count FROM "
        f'"{catalog}"."{schema}"."{table}" FOR VERSION AS OF {snapshot_id}'
    )
    try:
        sql = validate_workspace_query(sql, catalog)
    except InvalidWorkspaceQuery as exc:
        raise BackendUnavailableError(
            "The query engine cannot safely count this Iceberg snapshot."
        ) from exc
    session = QuerySession(
        query_id=uuid4().hex,
        actor=_actor(request),
        env=env,
        nessie_ref=nessie_ref,
        sql=sql,
        catalog=catalog,
        row_limit=1,
    )
    _remember_session(session)
    return _query_view(session)


async def _execute_session(session: QuerySession) -> None:
    session.status = "running"
    session.updated_at = datetime.now(UTC)

    def progress(query_id: str | None, next_uri: str | None) -> None:
        if query_id is not None:
            session.trino_query_id = query_id
        session.active_uri = next_uri

    try:
        statement = bound_workspace_query(session.sql, session.row_limit)
        if session.explain:
            statement = f"EXPLAIN {statement}"
        session.result = await execute_preview(
            statement,
            catalog=session.catalog,
            disconnected=lambda: asyncio.sleep(0, result=False),
            limit=session.row_limit,
            on_progress=progress,
            should_cancel=lambda: session.cancel_requested,
        )
        if get_process_settings().phlo_run_evidence_db_url:
            from phlo_api.incidents import persist_query_execution

            await asyncio.to_thread(
                persist_query_execution,
                query_id=session.id,
                env=session.env,
                actor=session.actor,
                nessie_ref=session.nessie_ref,
                statement=session.sql,
                executed_statement=statement,
                result=session.result,
                provider_query_id=session.trino_query_id,
            )
            session.evidence_available = True
        session.status = "completed"
    except asyncio.CancelledError:
        session.status = "cancelled"
    except PreviewLimitExceeded:
        session.status = "failed"
        session.error = "Query exceeded the configured row, time, or response limit."
    except PreviewQueryRejected as exc:
        session.status = "failed"
        session.error = str(exc)
    except PreviewUnavailable:
        session.status = "failed"
        session.error = "Query engine is unavailable or rejected the query."
    except Exception:
        session.status = "failed"
        session.error = "Query could not be completed."
    finally:
        session.updated_at = datetime.now(UTC)


def _saved_queries_path() -> Path:
    state_dir = project_root() / ".phlo" / "observatory"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "v1_saved_queries.json"


def _load_saved() -> list[SavedQuery]:
    items = load_collection(project_root(), _QUERY_COLLECTION, _saved_queries_path())
    try:
        return [SavedQuery.model_validate_json(json.dumps(item)) for item in items]
    except Exception as exc:
        from phlo.plugins.observatory_settings import StorageCorruptionError

        raise StorageCorruptionError("Query workspace durable state is unavailable") from exc


def _validate_saved_sql(sql: str, catalog: str) -> str:
    try:
        return validate_workspace_query(sql, catalog)
    except InvalidWorkspaceQuery as exc:
        raise HTTPException(
            status_code=422, detail={"error": "invalid_query", "message": str(exc)}
        ) from exc


def _idempotency_key(request: Request) -> str:
    key = request.headers.get("idempotency-key", "").strip()
    if not 1 <= len(key) <= 200:
        raise HTTPException(status_code=400, detail={"error": "idempotency_key_required"})
    return key


def _audit_saved(
    *,
    request: Request,
    operation: str,
    target: str,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> None:
    audit_operation(
        operation=operation,
        target=target,
        dry_run=False,
        auth={"subject": _actor(request), "scopes": []},
        payload=payload,
        result=result,
    )


def _saved_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise HTTPException(status_code=422, detail={"error": "saved_query_name_required"})
    return name


async def _unsupported_role_tables(catalog: str, tables: list[str]) -> set[str]:
    """Probe only connector-dependent role tables, under one five-second budget."""
    unsupported: set[str] = set()

    async def probe(table: str) -> None:
        quoted = catalog.replace('"', '""')
        try:
            await execute_preview(
                f'SELECT * FROM "{quoted}".information_schema."{table}" LIMIT 1',
                catalog=catalog,
                disconnected=lambda: asyncio.sleep(0, result=False),
                limit=1,
            )
        except PreviewQueryRejected as exc:
            if exc.error_name == "NOT_SUPPORTED":
                unsupported.add(table)
        except (PreviewLimitExceeded, PreviewUnavailable):
            pass  # An outage or denied permission is not proof of unsupported metadata.

    try:
        async with asyncio.timeout(5):
            for table in set(tables) & {"roles", "applicable_roles", "enabled_roles"}:
                await probe(table)
    except TimeoutError:
        pass
    return unsupported


@router.get("/query/catalog", response_model=QueryCatalog)
async def v1_query_catalog(request: Request, env: Environment) -> QueryCatalog:
    catalog, nessie_ref = _mapped_catalog(request, env)
    quoted = catalog.replace('"', '""')
    try:
        result = await execute_preview(
            f'SELECT table_schema, table_name FROM "{quoted}".information_schema.tables '
            "ORDER BY table_schema, table_name LIMIT 101",
            catalog=catalog,
            disconnected=lambda: asyncio.sleep(0, result=False),
            limit=100,
        )
    except (PreviewLimitExceeded, PreviewUnavailable) as exc:
        raise BackendUnavailableError("Query catalog is unavailable.") from exc
    schemas_by_name: dict[str, list[str]] = {}
    for row in result["rows"]:
        schema_name = str(row["table_schema"])
        schemas_by_name.setdefault(schema_name, []).append(str(row["table_name"]))
    system_tables = schemas_by_name.get("information_schema", [])
    unsupported = await _unsupported_role_tables(catalog, system_tables)
    if unsupported:
        schemas_by_name["information_schema"] = [
            table for table in system_tables if table not in unsupported
        ]
    return QueryCatalog(
        env=env,
        nessie_ref=nessie_ref,
        engine="trino",
        catalogs=[
            CatalogItem(
                name=catalog,
                schemas=[
                    CatalogSchema(name=name, tables=tables)
                    for name, tables in schemas_by_name.items()
                ],
                truncated=result["has_more"],
            )
        ],
    )


@router.get("/query/refs", response_model=QueryRefs)
async def v1_query_refs(request: Request, env: Environment) -> QueryRefs:
    catalog, nessie_ref = _mapped_catalog(request, env)
    return QueryRefs(env=env, items=[QueryRef(env=env, name=nessie_ref, catalog=catalog)])


@router.get("/query/engines", response_model=QueryEngines)
async def v1_query_engines(request: Request, env: Environment) -> QueryEngines:
    _mapped_catalog(request, env)
    return QueryEngines(env=env, items=[QueryEngine(id="trino", status="configured")])


@router.post("/queries", response_model=QuerySessionView, status_code=202)
async def v1_query_submit(
    request: Request, env: Environment, payload: QueryRequest
) -> QuerySessionView:
    catalog, nessie_ref = _prepare_query_attempt(
        request, env, operation="query.attempt", sql=payload.sql
    )
    try:
        sql = validate_workspace_query(payload.sql, catalog)
    except InvalidWorkspaceQuery as exc:
        _audit_query(
            request,
            operation="query.attempt",
            target=f"{env}:{nessie_ref}",
            sql=payload.sql,
            outcome="denied",
        )
        raise HTTPException(
            status_code=422, detail={"error": "invalid_query", "message": str(exc)}
        ) from exc
    query_id = uuid4().hex
    session = QuerySession(
        query_id=query_id,
        actor=_actor(request),
        env=env,
        nessie_ref=nessie_ref,
        sql=sql,
        catalog=catalog,
        row_limit=payload.row_limit,
    )
    _remember_session(session)
    return _query_view(session)


@router.post("/queries/explain", response_model=QuerySessionView, status_code=202)
async def v1_query_explain(
    request: Request, env: Environment, payload: QueryRequest
) -> QuerySessionView:
    catalog, nessie_ref = _prepare_query_attempt(
        request, env, operation="query.explain", sql=payload.sql
    )
    try:
        sql = validate_workspace_query(payload.sql, catalog)
    except InvalidWorkspaceQuery as exc:
        _audit_query(
            request,
            operation="query.explain",
            target=f"{env}:{nessie_ref}",
            sql=payload.sql,
            outcome="denied",
        )
        raise HTTPException(
            status_code=422, detail={"error": "invalid_query", "message": str(exc)}
        ) from exc
    query_id = uuid4().hex
    session = QuerySession(
        query_id=query_id,
        actor=_actor(request),
        env=env,
        nessie_ref=nessie_ref,
        sql=sql,
        catalog=catalog,
        row_limit=payload.row_limit,
        explain=True,
    )
    _remember_session(session)
    return _query_view(session)


def _session_for_actor(query_id: str, request: Request, env: Environment) -> QuerySession:
    session = _QUERY_SESSIONS.get(query_id)
    if session is None and get_process_settings().phlo_run_evidence_db_url:
        from phlo_api.incidents import load_query_execution

        record = load_query_execution(query_id, env, _actor(request))
        if record is not None:
            session = QuerySession(
                query_id=query_id,
                actor=_actor(request),
                env=env,
                nessie_ref=record["nessie_ref"],
                sql=record["statement"],
                catalog="",
                row_limit=100,
            )
            session.status = "completed"
            session.result = record["result"]
            session.created_at = session.updated_at = record["completed_at"]
            session.evidence_available = True
    if session is None or session.actor != _actor(request) or session.env != env:
        raise HTTPException(status_code=404, detail={"error": "query_not_found"})
    return session


@router.post("/queries/{query_id}/cancel", response_model=QuerySessionView)
async def v1_query_cancel(query_id: str, request: Request, env: Environment) -> QuerySessionView:
    session = _session_for_actor(query_id, request, env)
    if session.status in {"queued", "running"}:
        _audit_query(
            request,
            operation="query.cancel",
            target=f"{session.env}:{session.nessie_ref}:{query_id}",
            sql=session.sql,
            outcome="requested",
        )
        session.cancel_requested = True
        session.status = "cancelling"
        session.updated_at = datetime.now(UTC)
        if session.active_uri is not None and session.task is not None:
            session.task.cancel()
    return _query_view(session)


@router.get(
    "/queries/{query_id}/csv",
    responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
)
async def v1_query_csv(query_id: str, request: Request, env: Environment) -> Response:
    session = _session_for_actor(query_id, request, env)
    if session.status != "completed" or session.result is None:
        raise HTTPException(status_code=409, detail={"error": "query_not_complete"})
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    columns = session.result["columns"]
    writer.writerow([column["name"] for column in columns])
    for row in session.result["rows"]:
        writer.writerow([row[column["name"]] for column in columns])
    data = output.getvalue().encode("utf-8")
    if len(data) > 1_048_576:
        raise HTTPException(status_code=413, detail={"error": "csv_limit_exceeded"})
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="query-{query_id}.csv"'},
    )


@router.get("/queries/saved", response_model=SavedQueryPage)
async def v1_saved_queries(request: Request, env: Environment) -> SavedQueryPage:
    _, nessie_ref = _mapped_catalog(request, env)
    return SavedQueryPage(
        env=env,
        items=[item for item in _load_saved() if item.env == env and item.nessie_ref == nessie_ref],
    )


@router.post("/queries/saved", response_model=SavedQuery, status_code=201)
async def v1_saved_query_create(
    request: Request, env: Environment, payload: SavedQueryRequest
) -> SavedQuery:
    if payload.env != env:
        raise HTTPException(status_code=422, detail={"error": "environment_mismatch"})
    catalog, nessie_ref = _mapped_catalog(request, env)
    sql = _validate_saved_sql(payload.sql, catalog)
    now = datetime.now(UTC)
    item = SavedQuery(
        id=uuid4().hex,
        env=payload.env,
        nessie_ref=nessie_ref,
        name=_saved_name(payload.name),
        sql=sql,
        version=1,
        created_at=now,
        updated_at=now,
        metadata=safe_metadata(payload.metadata),
    )

    def create() -> dict[str, Any]:
        def append(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            saved = [SavedQuery.model_validate_json(json.dumps(value)) for value in items]
            if len(saved) >= _QUERY_LIMIT:
                raise HTTPException(status_code=409, detail={"error": "saved_query_limit_reached"})
            return [*items, item.model_dump(mode="json")]

        mutate_collection(project_root(), _QUERY_COLLECTION, _saved_queries_path(), append)
        return item.model_dump(mode="json")

    result = replay_or_execute(
        idempotency_key=_idempotency_key(request),
        operation="query.saved.create",
        target=f"{payload.env}:{nessie_ref}:saved_queries",
        execute=create,
        audit=lambda value: _audit_saved(
            request=request,
            operation="query.saved.create",
            target=f"{payload.env}:{nessie_ref}:{item.id}",
            payload={"sql_sha256": hashlib.sha256(sql.encode()).hexdigest()},
            result={"version": value["version"]},
        ),
    )
    return SavedQuery.model_validate_json(json.dumps(result))


@router.put("/queries/saved/{query_id}", response_model=SavedQuery)
async def v1_saved_query_update(
    query_id: str, request: Request, env: Environment, payload: SavedQueryUpdate
) -> SavedQuery:
    if payload.env != env:
        raise HTTPException(status_code=422, detail={"error": "environment_mismatch"})
    catalog, nessie_ref = _mapped_catalog(request, env)
    sql = _validate_saved_sql(payload.sql, catalog)

    def update(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        saved = [SavedQuery.model_validate_json(json.dumps(item)) for item in items]
        current = next((item for item in saved if item.id == query_id), None)
        if current is None or current.env != payload.env or current.nessie_ref != nessie_ref:
            raise HTTPException(status_code=404, detail={"error": "saved_query_not_found"})
        if current.version != payload.expected_version:
            raise HTTPException(status_code=409, detail={"error": "stale_saved_query"})
        replacement = current.model_copy(
            update={
                "name": _saved_name(payload.name),
                "sql": sql,
                "metadata": safe_metadata(payload.metadata),
                "version": current.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        return [
            replacement.model_dump(mode="json")
            if item.id == query_id
            else item.model_dump(mode="json")
            for item in saved
        ]

    def execute_update() -> dict[str, Any]:
        items = mutate_collection(project_root(), _QUERY_COLLECTION, _saved_queries_path(), update)
        return next(item for item in items if item["id"] == query_id)

    result = replay_or_execute(
        idempotency_key=_idempotency_key(request),
        operation="query.saved.update",
        target=f"{payload.env}:{nessie_ref}:{query_id}",
        execute=execute_update,
        audit=lambda value: _audit_saved(
            request=request,
            operation="query.saved.update",
            target=f"{payload.env}:{nessie_ref}:{query_id}",
            payload={"sql_sha256": hashlib.sha256(sql.encode()).hexdigest()},
            result={"version": value["version"]},
        ),
    )
    return SavedQuery.model_validate_json(json.dumps(result))


@router.delete("/queries/saved/{query_id}", status_code=204)
async def v1_saved_query_delete(
    query_id: str, request: Request, env: Environment, payload: SavedQueryDelete
) -> Response:
    if payload.env != env:
        raise HTTPException(status_code=422, detail={"error": "environment_mismatch"})
    _, nessie_ref = _mapped_catalog(request, env)

    def delete(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        saved = [SavedQuery.model_validate_json(json.dumps(item)) for item in items]
        current = next((item for item in saved if item.id == query_id), None)
        if current is None or current.env != payload.env or current.nessie_ref != nessie_ref:
            raise HTTPException(status_code=404, detail={"error": "saved_query_not_found"})
        if current.version != payload.expected_version:
            raise HTTPException(status_code=409, detail={"error": "stale_saved_query"})
        return [item.model_dump(mode="json") for item in saved if item.id != query_id]

    def execute_delete() -> dict[str, Any]:
        mutate_collection(project_root(), _QUERY_COLLECTION, _saved_queries_path(), delete)
        return {"deleted": True}

    replay_or_execute(
        idempotency_key=_idempotency_key(request),
        operation="query.saved.delete",
        target=f"{payload.env}:{nessie_ref}:{query_id}",
        execute=execute_delete,
        audit=lambda _: _audit_saved(
            request=request,
            operation="query.saved.delete",
            target=f"{payload.env}:{nessie_ref}:{query_id}",
            payload={"expected_version": payload.expected_version},
            result={"deleted": True},
        ),
    )
    return Response(status_code=204)


@router.get("/queries/{query_id}", response_model=QuerySessionView)
async def v1_query_result(query_id: str, request: Request, env: Environment) -> QuerySessionView:
    return _query_view(_session_for_actor(query_id, request, env))
