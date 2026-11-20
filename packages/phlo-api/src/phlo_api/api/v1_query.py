"""Ref-aware, bounded SQL workspace resources for ``/api/v1``."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import re
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response
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
from phlo_api.errors import (
    BackendUnavailableError,
    BadInputError,
    NotFoundError,
    UnprocessableInputError,
)
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


class CatalogObject(WireModel):
    name: str
    kind: Literal["schema", "table", "view", "column"]
    data_type: str | None = None
    nullable: bool | None = None


class CatalogObjects(WireModel):
    """One page of a lazily browsed catalog level: schemas, a schema's tables, or a table's columns."""

    env: Environment
    nessie_ref: str
    catalog: str
    schema_name: str | None = None
    table: str | None = None
    items: list[CatalogObject]
    next_offset: int | None


class CatalogColumn(WireModel):
    name: str
    data_type: str | None = None
    comment: str | None = None


class CatalogTableDetails(WireModel):
    """What a table or view is: columns with comments, its definition and, for Iceberg, size."""

    env: Environment
    nessie_ref: str
    catalog: str
    schema_name: str
    table: str
    kind: Literal["table", "view"]
    comment: str | None = None
    columns: list[CatalogColumn]
    ddl: str | None = None
    row_count: int | None = None
    snapshot_id: str | None = None
    committed_at: str | None = None


class CatalogSampleColumn(WireModel):
    name: str
    type: str | None = None


class CatalogSample(WireModel):
    env: Environment
    schema_name: str
    table: str
    columns: list[CatalogSampleColumn]
    rows: list[dict[str, Any]]
    has_more: bool


class CatalogHit(WireModel):
    schema_name: str
    table: str
    kind: Literal["table", "view", "column"]
    column: str | None = None
    data_type: str | None = None


class CatalogSearch(WireModel):
    env: Environment
    catalog: str
    q: str
    items: list[CatalogHit]
    truncated: bool


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


class QueryHistoryItem(WireModel):
    id: str
    env: Environment
    nessie_ref: str
    engine: Literal["trino"] = "trino"
    sql_hash: str
    completed_at: datetime


class QueryHistoryPage(WireModel):
    env: Environment
    status: Literal["partial", "unavailable"]
    reason: str | None
    items: list[QueryHistoryItem]
    truncated: bool


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


def _mapped_catalog(
    request: Request,
    env: Environment,
    allowed_query: frozenset[str] = frozenset({"env"}),
    *,
    sessions: bool = True,
) -> tuple[str, str]:
    """Resolve the env's catalog. ``sessions=False`` serves one-shot reads that keep no state,
    so they do not need the single-process assertion that query sessions do."""
    target = _target(request, env, allowed_query=allowed_query)
    try:
        deployment = get_deployment_settings()
    except ValueError as exc:
        raise BackendUnavailableError("Query deployment process configuration is invalid.") from exc
    if not deployment.query_single_replica:
        raise BackendUnavailableError(
            "Query workspace requires an explicitly single-replica API deployment."
        )
    if sessions and (not deployment.query_single_process or deployment.web_concurrency != 1):
        raise BackendUnavailableError(
            "Query workspace requires one explicitly asserted API process; active sessions are process-local."
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


def _load_saved() -> list[SavedQuery]:
    items = load_collection(project_root(), _QUERY_COLLECTION)
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


_BROWSE_PAGE = 100
_BROWSE_MAX_OFFSET = 100_000
_BROWSE_QUERY = frozenset({"env", "schema", "table", "offset"})


def _name_literal(value: str, label: str) -> str:
    """Quote a catalog object name as a SQL string literal for information_schema filters."""
    if not value or len(value) > 256 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise BadInputError(f"Give a valid {label} name.")
    return "'" + value.replace("'", "''") + "'"


async def _browse(catalog: str, sql: str, offset: int) -> tuple[list[dict[str, Any]], int | None]:
    if not 0 <= offset <= _BROWSE_MAX_OFFSET:
        raise BadInputError("The offset is outside the supported range.")
    try:
        result = await execute_preview(
            f"{sql} OFFSET {offset} LIMIT {_BROWSE_PAGE + 1}",
            catalog=catalog,
            disconnected=lambda: asyncio.sleep(0, result=False),
            limit=_BROWSE_PAGE,
        )
    except (PreviewLimitExceeded, PreviewUnavailable) as exc:
        raise BackendUnavailableError("Query catalog is unavailable.") from exc
    except PreviewQueryRejected as exc:
        raise BackendUnavailableError("The engine refused to list this catalog level.") from exc
    rows = list(result["rows"])[:_BROWSE_PAGE]
    return rows, offset + _BROWSE_PAGE if result["has_more"] else None


@router.get("/query/catalog/objects", response_model=CatalogObjects)
async def v1_query_catalog_objects(
    request: Request,
    env: Environment,
    schema: str | None = Query(default=None, max_length=256),
    table: str | None = Query(default=None, max_length=256),
    offset: int = Query(default=0, ge=0, le=_BROWSE_MAX_OFFSET),
) -> CatalogObjects:
    """Browse the lakehouse one level at a time, like a database explorer."""
    catalog, nessie_ref = _mapped_catalog(request, env, _BROWSE_QUERY, sessions=False)
    meta = '"' + catalog.replace('"', '""') + '".information_schema'
    if schema is None and table is not None:
        raise BadInputError("Give the schema that holds the table.")
    if schema is None:
        rows, next_offset = await _browse(
            catalog, f"SELECT schema_name FROM {meta}.schemata ORDER BY schema_name", offset
        )
        items = [CatalogObject(name=str(row["schema_name"]), kind="schema") for row in rows]
    elif table is None:
        rows, next_offset = await _browse(
            catalog,
            f"SELECT table_name, table_type FROM {meta}.tables "
            f"WHERE table_schema = {_name_literal(schema, 'schema')} ORDER BY table_name",
            offset,
        )
        items = [
            CatalogObject(
                name=str(row["table_name"]),
                kind="view" if str(row.get("table_type", "")).upper() == "VIEW" else "table",
            )
            for row in rows
        ]
    else:
        rows, next_offset = await _browse(
            catalog,
            f"SELECT column_name, data_type, is_nullable FROM {meta}.columns "
            f"WHERE table_schema = {_name_literal(schema, 'schema')} "
            f"AND table_name = {_name_literal(table, 'table')} ORDER BY ordinal_position",
            offset,
        )
        items = [
            CatalogObject(
                name=str(row["column_name"]),
                kind="column",
                data_type=str(row["data_type"]) if row.get("data_type") is not None else None,
                nullable=str(row.get("is_nullable", "")).upper() == "YES",
            )
            for row in rows
        ]
    return CatalogObjects(
        env=env,
        nessie_ref=nessie_ref,
        catalog=catalog,
        schema_name=schema,
        table=table,
        items=items,
        next_offset=next_offset,
    )


_TABLE_QUERY = frozenset({"env", "schema", "table"})
_SEARCH_QUERY = frozenset({"env", "q"})
_SAMPLE_ROWS = 50


def _quoted(catalog: str, schema: str, table: str) -> str:
    for value, label in ((schema, "schema"), (table, "table")):
        _name_literal(value, label)
    return ".".join('"' + part.replace('"', '""') + '"' for part in (catalog, schema, table))


async def _catalog_read(catalog: str, sql: str, limit: int) -> dict[str, Any] | None:
    """One bounded metadata read; ``None`` when the engine cannot answer it."""
    try:
        return await execute_preview(
            sql,
            catalog=catalog,
            disconnected=lambda: asyncio.sleep(0, result=False),
            limit=limit,
        )
    except (PreviewLimitExceeded, PreviewUnavailable, PreviewQueryRejected):
        return None


def _sql_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


async def _rebuilt_ddl(
    catalog: str,
    schema: str,
    table: str,
    kind: Literal["table", "view"],
    columns: list[CatalogColumn],
) -> str | None:
    """A definition from catalog metadata, for engines that refuse SHOW CREATE.

    Read-only preview identities are not owners, and Trino only shows a
    definition to an owner. The view text and the table's columns and storage
    properties are still readable, which is what someone opening the
    Definition tab wants to see.
    """
    name = _quoted(catalog, schema, table)
    if kind == "view":
        meta = '"' + catalog.replace('"', '""') + '".information_schema'
        view = _first(
            await _catalog_read(
                catalog,
                f"SELECT view_definition FROM {meta}.views WHERE table_schema = "
                f"{_name_literal(schema, 'schema')} AND table_name = {_name_literal(table, 'table')}",
                1,
            )
        )
        body = view.get("view_definition")
        if not body:
            return None
        return f"-- Rebuilt from information_schema\nCREATE VIEW {name} AS\n{str(body).rstrip()}"
    if not columns:
        return None
    lines = []
    for column in columns:
        line = '   "' + column.name.replace('"', '""') + '" ' + (column.data_type or "unknown")
        if column.comment:
            line += " COMMENT " + _sql_text(column.comment)
        lines.append(line)
    ddl = f"CREATE TABLE {name} (\n" + ",\n".join(lines) + "\n)"
    properties = '"' + f"{table}$properties".replace('"', '""') + '"'
    found = await _catalog_read(
        catalog,
        f'SELECT key, value FROM "{catalog.replace(chr(34), chr(34) * 2)}".'
        f'"{schema.replace(chr(34), chr(34) * 2)}".{properties}',
        50,
    )
    values = {str(row.get("key")): str(row.get("value")) for row in (found or {}).get("rows", [])}
    storage = []
    if values.get("format"):
        storage.append("format = " + _sql_text(values["format"].split("/")[-1]))
    if values.get("format-version", "").isdigit():
        storage.append("format_version = " + values["format-version"])
    if values.get("location"):
        storage.append("location = " + _sql_text(values["location"]))
    if storage:
        ddl += "\nWITH (\n   " + ",\n   ".join(storage) + "\n)"
    return "-- Rebuilt from the catalog; partitioning and sort order are not shown\n" + ddl


def _first(result: dict[str, Any] | None) -> dict[str, Any]:
    rows = (result or {}).get("rows") or []
    return rows[0] if rows else {}


async def _table_kind(catalog: str, schema: str, table: str) -> Literal["table", "view"]:
    meta = '"' + catalog.replace('"', '""') + '".information_schema'
    found = await _catalog_read(
        catalog,
        f"SELECT table_type FROM {meta}.tables WHERE table_schema = "
        f"{_name_literal(schema, 'schema')} AND table_name = {_name_literal(table, 'table')}",
        1,
    )
    if found is None:
        raise BackendUnavailableError("Query catalog is unavailable.")
    if not found["rows"]:
        raise NotFoundError("No table or view with that name in this environment.")
    return "view" if str(found["rows"][0].get("table_type", "")).upper() == "VIEW" else "table"


@router.get("/query/catalog/details", response_model=CatalogTableDetails)
async def v1_query_catalog_details(
    request: Request,
    env: Environment,
    schema: str = Query(max_length=256),
    table: str = Query(max_length=256),
) -> CatalogTableDetails:
    """Columns with comments, the definition, and (for Iceberg tables) rows and snapshot."""
    catalog, nessie_ref = _mapped_catalog(request, env, _TABLE_QUERY, sessions=False)
    name = _quoted(catalog, schema, table)
    kind = await _table_kind(catalog, schema, table)
    described = await _catalog_read(catalog, f"SHOW COLUMNS FROM {name}", 100)
    columns = [
        CatalogColumn(
            name=str(row.get("Column")),
            data_type=str(row["Type"]) if row.get("Type") is not None else None,
            comment=str(row["Comment"]) if row.get("Comment") else None,
        )
        for row in (described or {}).get("rows", [])
    ]
    shown = _first(
        await _catalog_read(
            catalog, f"SHOW CREATE {'VIEW' if kind == 'view' else 'TABLE'} {name}", 1
        )
    )
    ddl = next((str(value) for value in shown.values() if value), None) or await _rebuilt_ddl(
        catalog, schema, table, kind, columns
    )
    details = CatalogTableDetails(
        env=env,
        nessie_ref=nessie_ref,
        catalog=catalog,
        schema_name=schema,
        table=table,
        kind=kind,
        columns=columns,
        ddl=ddl,
    )
    if kind == "table":
        snapshots = '"' + f"{table}$snapshots".replace('"', '""') + '"'
        latest = _first(
            await _catalog_read(
                catalog,
                "SELECT CAST(snapshot_id AS VARCHAR) AS snapshot_id, "
                "CAST(committed_at AS VARCHAR) AS committed_at, "
                "summary['total-records'] AS total_records "
                f'FROM "{catalog.replace(chr(34), chr(34) * 2)}"."{schema.replace(chr(34), chr(34) * 2)}".{snapshots} '
                "ORDER BY committed_at DESC LIMIT 1",
                1,
            )
        )
        if latest:
            details.snapshot_id = latest.get("snapshot_id")
            details.committed_at = latest.get("committed_at")
            with suppress(TypeError, ValueError):
                details.row_count = int(latest["total_records"])
    comment = _first(
        await _catalog_read(
            catalog,
            "SELECT comment FROM system.metadata.table_comments WHERE catalog_name = "
            f"{_name_literal(catalog, 'catalog')} AND schema_name = {_name_literal(schema, 'schema')} "
            f"AND table_name = {_name_literal(table, 'table')}",
            1,
        )
    )
    details.comment = str(comment["comment"]) if comment.get("comment") else None
    return details


@router.get("/query/catalog/sample", response_model=CatalogSample)
async def v1_query_catalog_sample(
    request: Request,
    env: Environment,
    schema: str = Query(max_length=256),
    table: str = Query(max_length=256),
) -> CatalogSample:
    """The first rows of a table or view, like Snowsight's data preview."""
    catalog, _ = _mapped_catalog(request, env, _TABLE_QUERY, sessions=False)
    name = _quoted(catalog, schema, table)
    try:
        result = await execute_preview(
            f"SELECT * FROM {name} LIMIT {_SAMPLE_ROWS + 1}",
            catalog=catalog,
            disconnected=lambda: asyncio.sleep(0, result=False),
            limit=_SAMPLE_ROWS,
        )
    except PreviewQueryRejected as exc:
        raise UnprocessableInputError(str(exc)) from exc
    except (PreviewLimitExceeded, PreviewUnavailable) as exc:
        raise BackendUnavailableError("The data preview is unavailable.") from exc
    return CatalogSample(
        env=env,
        schema_name=schema,
        table=table,
        columns=[CatalogSampleColumn.model_validate(column) for column in result["columns"]],
        rows=result["rows"],
        has_more=result["has_more"],
    )


def _like(value: str) -> str:
    escaped = value.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return _name_literal(f"%{escaped}%", "search")


@router.get("/query/catalog/search", response_model=CatalogSearch)
async def v1_query_catalog_search(
    request: Request, env: Environment, q: str = Query(min_length=2, max_length=64)
) -> CatalogSearch:
    """Find schemas, tables, views and columns by name across the whole catalog."""
    catalog, _ = _mapped_catalog(request, env, _SEARCH_QUERY, sessions=False)
    meta = '"' + catalog.replace('"', '""') + '".information_schema'
    pattern = _like(q.strip())
    hidden = "table_schema <> 'information_schema'"
    tables = await _catalog_read(
        catalog,
        f"SELECT table_schema, table_name, table_type FROM {meta}.tables WHERE {hidden} AND "
        f"(lower(table_name) LIKE {pattern} ESCAPE '!' OR lower(table_schema) LIKE {pattern} ESCAPE '!') "
        "ORDER BY table_schema, table_name LIMIT 51",
        50,
    )
    columns = await _catalog_read(
        catalog,
        f"SELECT table_schema, table_name, column_name, data_type FROM {meta}.columns "
        f"WHERE {hidden} AND lower(column_name) LIKE {pattern} ESCAPE '!' "
        "ORDER BY table_schema, table_name, ordinal_position LIMIT 51",
        50,
    )
    if tables is None or columns is None:
        raise BackendUnavailableError("Query catalog is unavailable.")
    items = [
        CatalogHit(
            schema_name=str(row["table_schema"]),
            table=str(row["table_name"]),
            kind="view" if str(row.get("table_type", "")).upper() == "VIEW" else "table",
        )
        for row in tables["rows"]
    ] + [
        CatalogHit(
            schema_name=str(row["table_schema"]),
            table=str(row["table_name"]),
            kind="column",
            column=str(row["column_name"]),
            data_type=str(row["data_type"]) if row.get("data_type") is not None else None,
        )
        for row in columns["rows"]
    ]
    return CatalogSearch(
        env=env,
        catalog=catalog,
        q=q,
        items=items,
        truncated=bool(tables["has_more"] or columns["has_more"]),
    )


@router.get("/query/refs", response_model=QueryRefs)
async def v1_query_refs(request: Request, env: Environment) -> QueryRefs:
    catalog, nessie_ref = _mapped_catalog(request, env)
    return QueryRefs(env=env, items=[QueryRef(env=env, name=nessie_ref, catalog=catalog)])


@router.get("/query/engines", response_model=QueryEngines)
async def v1_query_engines(request: Request, env: Environment) -> QueryEngines:
    _mapped_catalog(request, env)
    return QueryEngines(env=env, items=[QueryEngine(id="trino", status="configured")])


@router.get("/queries/history", response_model=QueryHistoryPage)
async def v1_query_history(
    request: Request,
    env: Environment,
    limit: int = Query(default=50, ge=1, le=100),
) -> QueryHistoryPage:
    """Shared completed-execution metadata, never another actor's SQL or results."""
    target = _target(request, env, allowed_query=frozenset({"env", "limit"}))
    if not get_process_settings().phlo_run_evidence_db_url:
        return QueryHistoryPage(
            env=env,
            status="unavailable",
            reason="durable_query_evidence_not_configured",
            items=[],
            truncated=False,
        )
    from phlo_api.incidents import list_query_executions

    records = await asyncio.to_thread(list_query_executions, env, target.nessie_ref, limit + 1)
    return QueryHistoryPage(
        env=env,
        status="partial",
        reason="completed_workspace_executions_only",
        items=[QueryHistoryItem.model_validate(record) for record in records[:limit]],
        truncated=len(records) > limit,
    )


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

        mutate_collection(project_root(), _QUERY_COLLECTION, append)
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
        items = mutate_collection(project_root(), _QUERY_COLLECTION, update)
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
        mutate_collection(project_root(), _QUERY_COLLECTION, delete)
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
