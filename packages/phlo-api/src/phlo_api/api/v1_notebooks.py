"""Notebook files and Jupyter kernels for the Observatory Notebooks page.

Files are plain ``.ipynb`` documents under ``<project>/notebooks``. Kernels run
Python on the API host, so they are off unless the operator sets
``PHLO_NOTEBOOK_KERNELS=1`` on a single-process API. Trino SQL never runs in a
kernel: Observatory submits it through the validated query workspace, then
binds the completed rows into the kernel under the cell's result name.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import Field

from phlo_api import notebook_files as files
from phlo_api.api import v1_query
from phlo_api.api.authentication import get_request_principal
from phlo_api.api.operation_controls import audit_operation, project_root
from phlo_api.api.v1 import _target
from phlo_api.errors import (
    BackendUnavailableError,
    BadInputError,
    ConflictError,
    NotFoundError,
    UnprocessableInputError,
)
from phlo_api.notebook_kernels import (
    REGISTRY,
    ExecutionResult,
    KernelBusy,
    KernelLimitReached,
    KernelNotFound,
    KernelSession,
    KernelsUnavailable,
)
from phlo_api.settings import get_deployment_settings
from phlo_api.v1_contract import Environment, WireModel

router = APIRouter(tags=["v1 notebooks"])
_PATH_QUERY = frozenset({"env", "path"})
ResultName = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class NotebookSummary(WireModel):
    path: str
    size: int
    modified_at: datetime


class NotebookList(WireModel):
    env: Environment
    root: str
    items: list[NotebookSummary]
    truncated: bool


class NotebookCell(WireModel):
    id: str = Field(min_length=1, max_length=64)
    kind: Literal["markdown", "python", "sql"]
    source: str = Field(max_length=256 * 1024)
    name: str | None = ResultName
    engine: Literal["trino", "duckdb"] | None = None


class Notebook(WireModel):
    env: Environment
    path: str
    digest: str
    nessie_ref: str
    cells: list[NotebookCell]


class NotebookWrite(WireModel):
    cells: list[NotebookCell] = Field(max_length=500)
    expected_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class KernelStart(WireModel):
    path: str = Field(min_length=1, max_length=512)


class KernelView(WireModel):
    id: str
    env: Environment
    path: str
    status: Literal["idle", "busy"]
    execution_count: int


class KernelExecute(WireModel):
    code: str = Field(max_length=256 * 1024)
    name: str | None = ResultName


class KernelSql(WireModel):
    sql: str = Field(min_length=1, max_length=64 * 1024)
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class KernelBindQuery(WireModel):
    query_id: str = Field(min_length=1, max_length=64)
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class CellOutput(WireModel):
    type: Literal["stream", "result", "image", "error"]
    text: str | None = None
    name: str | None = None
    png: str | None = None
    ename: str | None = None


class TableColumn(WireModel):
    name: str
    type: str | None = None


class TablePreview(WireModel):
    columns: list[TableColumn]
    rows: list[dict[str, Any]]
    row_count: int


class CellRun(WireModel):
    status: Literal["ok", "error", "timeout"]
    execution_count: int | None
    duration_ms: int
    outputs: list[CellOutput]
    truncated: bool
    table: TablePreview | None = None


class KernelVariable(WireModel):
    name: str
    type: str
    shape: str | None = None
    preview: str | None = None


class KernelVariables(WireModel):
    items: list[KernelVariable]


def _actor(request: Request) -> str:
    principal = get_request_principal(request)
    return principal.subject if principal is not None else "development:anonymous"


def _audit(
    request: Request, operation: str, target: str, payload: dict[str, Any], outcome: str
) -> None:
    audit_operation(
        operation=operation,
        target=target,
        dry_run=False,
        auth={"subject": _actor(request), "scopes": []},
        payload=payload,
        result={"outcome": outcome},
    )


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _require_kernels() -> None:
    deployment = get_deployment_settings()
    if not deployment.notebook_kernels:
        raise BackendUnavailableError(
            "Notebook kernels are off. An operator can turn them on with PHLO_NOTEBOOK_KERNELS=1.",
            code="notebook_kernels_disabled",
        )
    if not deployment.query_single_process or deployment.web_concurrency != 1:
        raise BackendUnavailableError(
            "Notebook kernels need one API process; kernels are process-local.",
            code="notebook_kernels_disabled",
        )


def _kernel(request: Request, env: Environment, kernel_id: str) -> KernelSession:
    _target(request, env)
    _require_kernels()
    try:
        session = REGISTRY.get(kernel_id, _actor(request))
    except KernelNotFound as exc:
        raise NotFoundError("This kernel has stopped. Start it again from the notebook.") from exc
    if session.env != env:
        raise NotFoundError("This kernel has stopped. Start it again from the notebook.")
    return session


def _kernel_view(session: KernelSession) -> KernelView:
    return KernelView.model_validate(
        {
            "id": session.id,
            "env": session.env,
            "path": session.path,
            "status": session.status,
            "execution_count": session.execution_count,
        }
    )


def _run_view(result: ExecutionResult) -> CellRun:
    return CellRun(
        status=result.status,
        execution_count=result.execution_count,
        duration_ms=result.duration_ms,
        outputs=[CellOutput(**vars(output)) for output in result.outputs],
        truncated=result.truncated,
        table=TablePreview.model_validate(result.table) if result.table else None,
    )


def _document(env: Environment, nessie_ref: str, document: files.NotebookDocument) -> Notebook:
    return Notebook(
        env=env,
        path=document.path,
        digest=document.digest,
        nessie_ref=nessie_ref,
        cells=[NotebookCell(**vars(cell)) for cell in document.cells],
    )


@router.get("/notebooks", response_model=NotebookList)
async def v1_notebooks(request: Request, env: Environment) -> NotebookList:
    _target(request, env)
    items, truncated = files.list_notebooks(project_root())
    return NotebookList(
        env=env,
        root=files.NOTEBOOK_DIR,
        items=[NotebookSummary(**vars(item)) for item in items],
        truncated=truncated,
    )


@router.get("/notebooks/content", response_model=Notebook)
async def v1_notebook(request: Request, env: Environment, path: str) -> Notebook:
    target = _target(request, env, allowed_query=_PATH_QUERY)
    try:
        document = files.read_notebook(project_root(), path)
    except files.NotebookPathError as exc:
        raise BadInputError(str(exc)) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("No notebook at that path.") from exc
    except files.NotebookInvalid as exc:
        raise UnprocessableInputError(str(exc)) from exc
    return _document(env, target.nessie_ref, document)


@router.put("/notebooks/content", response_model=Notebook)
async def v1_notebook_save(
    request: Request, env: Environment, path: str, payload: NotebookWrite
) -> Notebook:
    target = _target(request, env, allowed_query=_PATH_QUERY)
    cells = [files.NotebookCell(**cell.model_dump()) for cell in payload.cells]
    try:
        document = files.write_notebook(project_root(), path, cells, payload.expected_digest)
    except files.NotebookPathError as exc:
        raise BadInputError(str(exc)) from exc
    except files.NotebookConflict as exc:
        _audit(request, "notebook.save", f"{env}:{path}", {"cells": len(cells)}, "conflict")
        raise ConflictError(
            "This notebook changed since you opened it. Reload it before saving.",
            code="notebook_changed",
        ) from exc
    except files.NotebookInvalid as exc:
        raise UnprocessableInputError(str(exc)) from exc
    _audit(
        request,
        "notebook.save",
        f"{env}:{path}",
        {"cells": len(cells), "digest": document.digest},
        "saved",
    )
    return _document(env, target.nessie_ref, document)


@router.get("/notebooks/kernels", response_model=list[KernelView])
async def v1_notebook_kernels(request: Request, env: Environment) -> list[KernelView]:
    _target(request, env)
    _require_kernels()
    return [
        _kernel_view(session)
        for session in REGISTRY.for_actor(_actor(request))
        if session.env == env
    ]


@router.post("/notebooks/kernels", response_model=KernelView, status_code=201)
async def v1_notebook_kernel_start(
    request: Request, env: Environment, payload: KernelStart
) -> KernelView:
    _target(request, env)
    _require_kernels()
    try:
        files.resolve_notebook_path(project_root(), payload.path)
    except files.NotebookPathError as exc:
        raise BadInputError(str(exc)) from exc
    try:
        session = await REGISTRY.start(actor=_actor(request), env=env, path=payload.path)
    except KernelsUnavailable as exc:
        raise BackendUnavailableError(str(exc), code="notebook_kernels_unavailable") from exc
    except KernelLimitReached as exc:
        raise ConflictError(str(exc), code="notebook_kernel_limit") from exc
    _audit(
        request, "notebook.kernel.start", f"{env}:{payload.path}", {"kernel": session.id}, "started"
    )
    return _kernel_view(session)


async def _guarded(
    request: Request, operation: str, session: KernelSession, payload: dict[str, Any], run: Any
) -> CellRun:
    try:
        result = await run
    except KernelBusy as exc:
        raise ConflictError(str(exc), code="notebook_kernel_busy") from exc
    _audit(request, operation, f"{session.env}:{session.path}", payload, result.status)
    return _run_view(result)


@router.post("/notebooks/kernels/{kernel_id}/execute", response_model=CellRun)
async def v1_notebook_kernel_execute(
    request: Request, env: Environment, kernel_id: str, payload: KernelExecute
) -> CellRun:
    session = _kernel(request, env, kernel_id)
    return await _guarded(
        request,
        "notebook.cell.run",
        session,
        {"kernel": kernel_id, "code_sha256": _sha(payload.code)},
        REGISTRY.execute(session, payload.code, preview=payload.name),
    )


@router.post("/notebooks/kernels/{kernel_id}/sql", response_model=CellRun)
async def v1_notebook_kernel_sql(
    request: Request, env: Environment, kernel_id: str, payload: KernelSql
) -> CellRun:
    session = _kernel(request, env, kernel_id)
    return await _guarded(
        request,
        "notebook.cell.duckdb",
        session,
        {"kernel": kernel_id, "sql_sha256": _sha(payload.sql)},
        REGISTRY.duckdb(session, payload.name, payload.sql),
    )


@router.post("/notebooks/kernels/{kernel_id}/bind", response_model=CellRun)
async def v1_notebook_kernel_bind(
    request: Request, env: Environment, kernel_id: str, payload: KernelBindQuery
) -> CellRun:
    """Load a completed query-workspace result into the kernel under ``name``."""
    session = _kernel(request, env, kernel_id)
    query = v1_query._QUERY_SESSIONS.get(payload.query_id)
    if (
        query is None
        or query.actor != session.actor
        or query.env != env
        or query.explain
        or query.status != "completed"
        or query.result is None
    ):
        raise NotFoundError("That query result is not available to bind. Run the cell again.")
    return await _guarded(
        request,
        "notebook.cell.bind",
        session,
        {"kernel": kernel_id, "query": payload.query_id, "name": payload.name},
        REGISTRY.bind_rows(session, payload.name, query.result),
    )


@router.get("/notebooks/kernels/{kernel_id}/variables", response_model=KernelVariables)
async def v1_notebook_kernel_variables(
    request: Request, env: Environment, kernel_id: str
) -> KernelVariables:
    session = _kernel(request, env, kernel_id)
    if session.lock.locked():
        raise ConflictError("The kernel is still running a cell.", code="notebook_kernel_busy")
    items = await REGISTRY.variables(session)
    return KernelVariables(items=[KernelVariable.model_validate(item) for item in items])


@router.post("/notebooks/kernels/{kernel_id}/interrupt", response_model=KernelView)
async def v1_notebook_kernel_interrupt(
    request: Request, env: Environment, kernel_id: str
) -> KernelView:
    session = _kernel(request, env, kernel_id)
    await REGISTRY.interrupt(session)
    _audit(
        request,
        "notebook.kernel.interrupt",
        f"{env}:{session.path}",
        {"kernel": kernel_id},
        "interrupted",
    )
    return _kernel_view(session)


@router.post("/notebooks/kernels/{kernel_id}/restart", response_model=KernelView)
async def v1_notebook_kernel_restart(
    request: Request, env: Environment, kernel_id: str
) -> KernelView:
    session = _kernel(request, env, kernel_id)
    await REGISTRY.restart(session)
    _audit(
        request,
        "notebook.kernel.restart",
        f"{env}:{session.path}",
        {"kernel": kernel_id},
        "restarted",
    )
    return _kernel_view(session)


@router.delete("/notebooks/kernels/{kernel_id}", status_code=204)
async def v1_notebook_kernel_stop(request: Request, env: Environment, kernel_id: str) -> Response:
    session = _kernel(request, env, kernel_id)
    await REGISTRY.shutdown(session)
    _audit(
        request, "notebook.kernel.stop", f"{env}:{session.path}", {"kernel": kernel_id}, "stopped"
    )
    return Response(status_code=204)
