"""Notebook files, Jupyter kernels, schedules and publishing for the Observatory Notebooks page.

Files are plain ``.ipynb`` documents under ``<project>/notebooks``. Kernels run
Python on the API host, so they are off unless the operator sets
``PHLO_NOTEBOOK_KERNELS=1`` on a single-process API.
Trino SQL never runs in a kernel: the API runs it through the validated
read-only query path, then binds the rows into the kernel under the cell's
result name.

Cells run in the background: starting a run returns at once and the page polls
it, seeing output as it arrives. Each cell's last output is kept outside git so
reopening a notebook shows its results.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Request, Response
from pydantic import Field

from phlo.rbac.models import CanonicalAction
from phlo_api import notebook_files as files
from phlo_api import notebook_git, notebook_promote
from phlo_api import notebook_state as state
from phlo_api.api.authentication import get_request_principal
from phlo_api.api.operation_controls import audit_operation, project_root
from phlo_api.api.v1 import _target, _targets, is_allowed
from phlo_api.api.v1_query_sql import InvalidWorkspaceQuery, validate_workspace_query
from phlo_api.errors import (
    BackendUnavailableError,
    BadInputError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableInputError,
)
from phlo_api.notebook_kernels import (
    REGISTRY,
    ExecutionResult,
    KernelBusy,
    KernelLimitReached,
    KernelNotFound,
    KernelNotReady,
    KernelOutput,
    KernelRecord,
    KernelsUnavailable,
    PackagesDisabled,
    PackagesInvalid,
    RunRecord,
    normalize_packages,
)
from phlo_api.notebook_runner import CellWork, run_cell, run_notebook
from phlo_api.observatory_api.v1_preview import (
    PreviewBudget,
    PreviewLimitExceeded,
    PreviewQueryRejected,
    PreviewUnavailable,
    execute_preview,
    preview_catalog,
)
from phlo_api.settings import ApiDeploymentSettings, get_deployment_settings
from phlo_api.v1_contract import Environment, WireModel

router = APIRouter(tags=["v1 notebooks"])
_PATH_QUERY = frozenset({"env", "path"})
_DELETE_QUERY = frozenset({"env", "path", "digest"})
ResultName = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
Digest = Field(pattern=r"^[a-f0-9]{64}$")


# -- wire models ----------------------------------------------------------------


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


class NotebookScheduleView(WireModel):
    cron: str
    env: Environment
    owner: str
    enabled: bool


class NotebookAccess(WireModel):
    edit: bool
    run: bool
    schedule: bool
    publish: bool


class Notebook(WireModel):
    env: Environment
    path: str
    digest: str
    nessie_ref: str
    cells: list[NotebookCell]
    packages: list[str]
    schedule: NotebookScheduleView | None
    access: NotebookAccess


class NotebookWrite(WireModel):
    cells: list[NotebookCell] = Field(max_length=500)
    expected_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    packages: list[str] = Field(default_factory=list, max_length=50)


class NotebookMove(WireModel):
    to: str = Field(min_length=1, max_length=512)
    expected_digest: str = Digest


class NotebookScheduleWrite(WireModel):
    cron: str | None = Field(default=None, max_length=120)
    env: Environment | None = None
    enabled: bool = True
    expected_digest: str = Digest


class KernelStart(WireModel):
    path: str = Field(min_length=1, max_length=512)


class KernelView(WireModel):
    id: str
    env: Environment
    path: str
    status: Literal["installing", "starting", "failed", "idle", "busy"]
    message: str | None
    packages: list[str]
    execution_count: int


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
    status: Literal["running", "ok", "error", "timeout", "cancelled"]
    execution_count: int | None
    duration_ms: int
    outputs: list[CellOutput]
    truncated: bool
    table: TablePreview | None = None


class RunStart(WireModel):
    kind: Literal["python", "duckdb", "trino"]
    source: str = Field(max_length=256 * 1024)
    name: str | None = ResultName
    cell_id: str | None = Field(default=None, min_length=1, max_length=64)
    path: str | None = Field(default=None, max_length=512)


class RunView(WireModel):
    id: str
    kernel: str
    status: Literal["running", "ok", "error", "timeout", "cancelled"]
    result: CellRun | None


class KernelVariable(WireModel):
    name: str
    type: str
    shape: str | None = None
    preview: str | None = None


class KernelVariables(WireModel):
    items: list[KernelVariable]


class KernelPackage(WireModel):
    name: str
    version: str
    source: Literal["notebook", "base"]


class KernelPackages(WireModel):
    requested: list[str]
    items: list[KernelPackage]


class CompleteRequest(WireModel):
    code: str = Field(max_length=256 * 1024)
    cursor: int = Field(ge=0)


class Completions(WireModel):
    matches: list[str]
    cursor_start: int
    cursor_end: int


class SavedOutput(WireModel):
    cell_id: str
    source_sha256: str
    actor: str
    finished_at: datetime
    run: CellRun


class NotebookOutputs(WireModel):
    items: list[SavedOutput]
    retention_days: int


class NotebookRunCell(WireModel):
    cell_id: str
    status: str
    duration_ms: int
    error: str | None = None


class NotebookRun(WireModel):
    id: str
    trigger: Literal["manual", "schedule"]
    actor: str
    env: Environment
    status: Literal["running", "ok", "failed"]
    started_at: datetime
    finished_at: datetime | None
    message: str | None
    cells: list[NotebookRunCell]


class NotebookRuns(WireModel):
    items: list[NotebookRun]
    schedules_enabled: bool


class GitState(WireModel):
    enabled: bool
    branch: str | None
    protected: bool
    changed: list[str]
    message: str | None = None


class GitCommitRequest(WireModel):
    paths: list[str] = Field(min_length=1, max_length=50)
    message: str = Field(min_length=1, max_length=2000)


class GitCommitView(WireModel):
    branch: str
    commit: str


class PromoteRequest(WireModel):
    cell_id: str = Field(min_length=1, max_length=64)
    model: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,63}$")
    apply: bool = False


class PromotedFile(WireModel):
    path: str
    content: str
    exists: bool


class PromoteView(WireModel):
    files: list[PromotedFile]
    applied: bool


# -- helpers --------------------------------------------------------------------


def _actor(request: Request) -> str:
    principal = get_request_principal(request)
    return principal.subject if principal is not None else "development:anonymous"


def _author(request: Request) -> str:
    principal = get_request_principal(request)
    subject = principal.subject if principal is not None else "observatory"
    email = getattr(principal, "email", None) or f"{subject}@users.phlo.invalid"
    name = subject.replace("<", "").replace(">", "")[:120]
    return f"{name} <{str(email).replace('<', '').replace('>', '')}>"


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


def _deployment() -> ApiDeploymentSettings:
    try:
        return get_deployment_settings()
    except ValueError as exc:
        raise BackendUnavailableError("Notebook settings are invalid.") from exc


def _registry(deployment: ApiDeploymentSettings) -> Any:
    REGISTRY.configure(
        root=Path(deployment.notebook_kernel_dir) if deployment.notebook_kernel_dir else None,
        package_index=deployment.notebook_package_index,
    )
    return REGISTRY


def _require_kernels() -> ApiDeploymentSettings:
    deployment = _deployment()
    if not deployment.notebook_kernels:
        raise BackendUnavailableError(
            "Notebook kernels are off. An operator can turn them on with PHLO_NOTEBOOK_KERNELS=1.",
            code="notebook_kernels_disabled",
        )
    if not deployment.query_single_process or deployment.web_concurrency != 1:
        raise BackendUnavailableError(
            "Notebook kernels need one API process (PHLO_V1_QUERY_SINGLE_PROCESS=1, "
            "WEB_CONCURRENCY=1).",
            code="notebook_kernels_disabled",
        )
    _registry(deployment)
    return deployment


def _require_query(request: Request, env: Environment) -> None:
    """Trino cells read lakehouse data: the same permission as the Query page."""
    if not is_allowed(request, env, CanonicalAction.DATASET_QUERY.value, "project"):
        raise ForbiddenError(
            "You can run Python here but not query the lakehouse.", code="notebook_query_denied"
        )


def _kernel(request: Request, env: Environment, kernel_id: str) -> KernelRecord:
    _target(request, env)
    _require_kernels()
    try:
        record = REGISTRY.get(kernel_id, _actor(request))
    except KernelNotFound as exc:
        raise NotFoundError("This kernel has stopped. Start it again from the notebook.") from exc
    except KernelsUnavailable as exc:
        raise BackendUnavailableError(str(exc), code="notebook_kernels_unavailable") from exc
    if record.env != env:
        raise NotFoundError("This kernel has stopped. Start it again from the notebook.")
    return record


def _kernel_view(record: KernelRecord) -> KernelView:
    return KernelView.model_validate(
        {
            "id": record.id,
            "env": record.env,
            "path": record.path,
            "status": REGISTRY.status(record),
            "message": record.message,
            "packages": record.packages,
            "execution_count": record.execution_count,
        }
    )


def _cell_run(result: dict[str, Any] | None) -> CellRun | None:
    return CellRun.model_validate(result) if result else None


def _run_view(run: RunRecord) -> RunView:
    return RunView(id=run.id, kernel=run.kernel, status=run.status, result=_cell_run(run.result))


def _access(request: Request, env: Environment) -> NotebookAccess:
    def allowed(action: CanonicalAction) -> bool:
        return is_allowed(request, env, action.value, "project")

    return NotebookAccess(
        edit=allowed(CanonicalAction.OBJECT_WRITE),
        run=allowed(CanonicalAction.RUN_EXECUTE),
        schedule=allowed(CanonicalAction.RUN_MANAGE),
        publish=allowed(CanonicalAction.ASSET_MANAGE),
    )


def _document(
    request: Request, env: Environment, nessie_ref: str, document: files.NotebookDocument
) -> Notebook:
    schedule = document.settings.schedule
    return Notebook(
        env=env,
        path=document.path,
        digest=document.digest,
        nessie_ref=nessie_ref,
        cells=[NotebookCell(**vars(cell)) for cell in document.cells],
        packages=list(document.settings.packages),
        schedule=NotebookScheduleView.model_validate(vars(schedule)) if schedule else None,
        access=_access(request, env),
    )


def _read(path: str) -> files.NotebookDocument:
    try:
        return files.read_notebook(project_root(), path)
    except files.NotebookPathError as exc:
        raise BadInputError(str(exc)) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("No notebook at that path.") from exc
    except files.NotebookInvalid as exc:
        raise UnprocessableInputError(str(exc), code="notebook_invalid") from exc


def _changed() -> ConflictError:
    return ConflictError(
        "This notebook changed since you opened it. Reload it before saving.",
        code="notebook_changed",
    )


def notebook_budget(deployment: ApiDeploymentSettings) -> PreviewBudget:
    return PreviewBudget(
        rows=deployment.notebook_max_rows,
        response_bytes=64 * 1024 * 1024,
        page_bytes=32 * 1024 * 1024,
        seconds=min(float(deployment.notebook_cell_timeout), 600.0),
        admission_wait=30.0,
    )


def trino_rows(deployment: ApiDeploymentSettings) -> Callable[..., Any]:
    """Run notebook Trino SQL through the validated, ref-scoped read-only query path."""
    budget = notebook_budget(deployment)

    async def run(env: str, sql: str, cancelled: Callable[[], bool]) -> dict[str, Any]:
        targets = _targets()
        if env not in targets:
            raise PreviewUnavailable("Environment is not configured.")
        catalog = preview_catalog(env, targets[env].nessie_ref)
        statement = validate_workspace_query(sql, catalog)
        return await execute_preview(
            statement,
            catalog=catalog,
            disconnected=_never,
            limit=budget.rows,
            should_cancel=cancelled,
            budget=budget,
        )

    return run


async def _never() -> bool:
    return False


# -- files ----------------------------------------------------------------------


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
    return _document(request, env, target.nessie_ref, _read(path))


@router.put("/notebooks/content", response_model=Notebook)
async def v1_notebook_save(
    request: Request, env: Environment, path: str, payload: NotebookWrite
) -> Notebook:
    """Save cells and packages; the schedule is kept as it is (it has its own route)."""
    target = _target(request, env, allowed_query=_PATH_QUERY)
    cells = [files.NotebookCell(**cell.model_dump()) for cell in payload.cells]
    try:
        packages = tuple(normalize_packages(payload.packages))
    except PackagesInvalid as exc:
        raise UnprocessableInputError(str(exc), code="notebook_invalid") from exc
    schedule = None
    if payload.expected_digest is not None:
        with suppress(FileNotFoundError, files.NotebookInvalid, files.NotebookPathError):
            schedule = files.read_notebook(project_root(), path).settings.schedule
    settings = files.NotebookSettings(packages=packages, schedule=schedule)
    try:
        document = files.write_notebook(
            project_root(), path, cells, payload.expected_digest, settings
        )
    except files.NotebookPathError as exc:
        raise BadInputError(str(exc)) from exc
    except files.NotebookConflict as exc:
        _audit(request, "notebook.save", f"{env}:{path}", {"cells": len(cells)}, "conflict")
        raise _changed() from exc
    except files.NotebookInvalid as exc:
        raise UnprocessableInputError(str(exc), code="notebook_invalid") from exc
    _audit(
        request,
        "notebook.save",
        f"{env}:{path}",
        {"cells": len(cells), "digest": document.digest, "packages": list(packages)},
        "saved",
    )
    return _document(request, env, target.nessie_ref, document)


@router.post("/notebooks/move", response_model=Notebook)
async def v1_notebook_move(
    request: Request, env: Environment, path: str, payload: NotebookMove
) -> Notebook:
    """Rename or move a notebook; its saved outputs and run history move with it."""
    target = _target(request, env, allowed_query=_PATH_QUERY)
    project = project_root()
    try:
        document = files.move_notebook(project, path, payload.to, payload.expected_digest)
    except files.NotebookPathError as exc:
        raise BadInputError(str(exc)) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("No notebook at that path.") from exc
    except files.NotebookConflict as exc:
        raise ConflictError(
            "A notebook already exists there, or this one changed since you opened it.",
            code="notebook_changed",
        ) from exc
    except files.NotebookInvalid as exc:
        raise UnprocessableInputError(str(exc), code="notebook_invalid") from exc
    state.move_state(project, path, payload.to)
    _audit(request, "notebook.move", f"{env}:{path}", {"to": payload.to}, "moved")
    return _document(request, env, target.nessie_ref, document)


@router.delete("/notebooks/content", status_code=204)
async def v1_notebook_delete(
    request: Request, env: Environment, path: str, digest: str
) -> Response:
    """Delete a notebook, only if it is still the version the caller last saw."""
    _target(request, env, allowed_query=_DELETE_QUERY)
    project = project_root()
    try:
        files.delete_notebook(project, path, digest)
    except files.NotebookPathError as exc:
        raise BadInputError(str(exc)) from exc
    except FileNotFoundError as exc:
        raise NotFoundError("No notebook at that path.") from exc
    except files.NotebookConflict as exc:
        raise _changed() from exc
    state.delete_state(project, path)
    _audit(request, "notebook.delete", f"{env}:{path}", {"digest": digest}, "deleted")
    return Response(status_code=204)


@router.put("/notebooks/schedule", response_model=Notebook)
async def v1_notebook_schedule(
    request: Request, env: Environment, path: str, payload: NotebookScheduleWrite
) -> Notebook:
    """Set or clear a notebook's cron schedule; runs execute as the person who set it."""
    target = _target(request, env, allowed_query=_PATH_QUERY)
    current = _read(path)
    schedule = None
    if payload.cron:
        run_env = payload.env or env
        if run_env not in _targets():
            raise UnprocessableInputError("Environment is not configured.", code="notebook_invalid")
        try:
            cron = files.validate_cron(payload.cron)
        except files.NotebookInvalid as exc:
            raise UnprocessableInputError(str(exc), code="notebook_invalid") from exc
        schedule = files.NotebookSchedule(
            cron=cron, env=run_env, owner=_actor(request), enabled=payload.enabled
        )
    settings = files.NotebookSettings(packages=current.settings.packages, schedule=schedule)
    try:
        document = files.write_notebook(
            project_root(), path, list(current.cells), payload.expected_digest, settings
        )
    except files.NotebookConflict as exc:
        raise _changed() from exc
    except files.NotebookInvalid as exc:
        raise UnprocessableInputError(str(exc), code="notebook_invalid") from exc
    _audit(
        request,
        "notebook.schedule",
        f"{env}:{path}",
        {"cron": schedule.cron if schedule else None, "enabled": payload.enabled},
        "scheduled" if schedule else "cleared",
    )
    return _document(request, env, target.nessie_ref, document)


@router.get("/notebooks/outputs", response_model=NotebookOutputs)
async def v1_notebook_outputs(request: Request, env: Environment, path: str) -> NotebookOutputs:
    """Each cell's last output, with the hash of the source that produced it."""
    _target(request, env, allowed_query=_PATH_QUERY)
    try:
        files.resolve_notebook_path(project_root(), path)
    except files.NotebookPathError as exc:
        raise BadInputError(str(exc)) from exc
    days = _deployment().notebook_output_days
    items = []
    for cell_id, value in state.read_outputs(project_root(), path, days).items():
        try:
            items.append(
                SavedOutput(
                    cell_id=cell_id,
                    source_sha256=value["source_sha256"],
                    actor=value["actor"],
                    finished_at=datetime.fromtimestamp(float(value["finished_at"]), UTC),
                    run=CellRun.model_validate(value["run"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return NotebookOutputs(items=items, retention_days=days)


@router.get("/notebooks/runs", response_model=NotebookRuns)
async def v1_notebook_runs(request: Request, env: Environment, path: str) -> NotebookRuns:
    """Whole-notebook runs, newest first: Run in background and scheduled runs."""
    _target(request, env, allowed_query=_PATH_QUERY)
    try:
        files.resolve_notebook_path(project_root(), path)
    except files.NotebookPathError as exc:
        raise BadInputError(str(exc)) from exc
    items = []
    for raw in state.read_runs(project_root(), path):
        try:
            items.append(
                NotebookRun.model_validate(
                    {
                        **raw,
                        "started_at": datetime.fromtimestamp(float(raw["started_at"]), UTC),
                        "finished_at": datetime.fromtimestamp(float(raw["finished_at"]), UTC)
                        if raw.get("finished_at")
                        else None,
                    }
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return NotebookRuns(items=items, schedules_enabled=_deployment().notebook_schedules)


@router.post("/notebooks/runs", response_model=NotebookRun, status_code=202)
async def v1_notebook_run_start(request: Request, env: Environment, path: str) -> NotebookRun:
    """Run the saved notebook top to bottom in its own kernel, in the background."""
    _target(request, env, allowed_query=_PATH_QUERY)
    deployment = _require_kernels()
    document = _read(path)
    if any(cell.kind == "sql" and cell.engine != "duckdb" for cell in document.cells):
        _require_query(request, env)
    actor = _actor(request)
    run_id = uuid4().hex
    started = time.time()
    REGISTRY.spawn(
        run_notebook(
            REGISTRY,
            project=project_root(),
            path=document.path,
            env=env,
            actor=actor,
            trigger="manual",
            trino=trino_rows(deployment),
            timeout=float(deployment.notebook_cell_timeout),
            output_days=deployment.notebook_output_days,
            allow_packages=deployment.notebook_packages,
            run_id=run_id,
        )
    )
    _audit(request, "notebook.run", f"{env}:{path}", {"run": run_id}, "started")
    return NotebookRun(
        id=run_id,
        trigger="manual",
        actor=actor,
        env=env,
        status="running",
        started_at=datetime.fromtimestamp(started, UTC),
        finished_at=None,
        message=None,
        cells=[],
    )


# -- publishing -----------------------------------------------------------------


def _protected(deployment: ApiDeploymentSettings) -> set[str]:
    return {
        branch.strip()
        for branch in deployment.notebook_git_protected_branches.split(",")
        if branch.strip()
    }


@router.get("/notebooks/git", response_model=GitState)
async def v1_notebook_git(request: Request, env: Environment) -> GitState:
    _target(request, env)
    deployment = _deployment()
    if not deployment.notebook_git_commit:
        return GitState(
            enabled=False,
            branch=None,
            protected=True,
            changed=[],
            message="An operator can allow commits from Observatory with PHLO_NOTEBOOK_GIT_COMMIT=1.",
        )
    try:
        current = await notebook_git.status(project_root(), _protected(deployment))
    except notebook_git.GitUnavailable as exc:
        return GitState(enabled=False, branch=None, protected=True, changed=[], message=str(exc))
    return GitState(
        enabled=True, branch=current.branch, protected=current.protected, changed=current.changed
    )


@router.post("/notebooks/git/commit", response_model=GitCommitView)
async def v1_notebook_git_commit(
    request: Request, env: Environment, payload: GitCommitRequest
) -> GitCommitView:
    _target(request, env)
    deployment = _deployment()
    if not deployment.notebook_git_commit:
        raise BackendUnavailableError(
            "Commits from Observatory are off. An operator can allow them with "
            "PHLO_NOTEBOOK_GIT_COMMIT=1.",
            code="notebook_git_disabled",
        )
    try:
        branch, sha = await notebook_git.commit(
            project_root(),
            payload.paths,
            payload.message,
            author=_author(request),
            protected=_protected(deployment),
        )
    except files.NotebookPathError as exc:
        raise BadInputError(str(exc)) from exc
    except notebook_git.GitRefused as exc:
        raise ConflictError(str(exc), code="notebook_git_refused") from exc
    except notebook_git.GitUnavailable as exc:
        raise BackendUnavailableError(str(exc), code="notebook_git_unavailable") from exc
    _audit(
        request,
        "notebook.git.commit",
        f"{env}:{branch}",
        {"paths": sorted(payload.paths), "commit": sha},
        "committed",
    )
    return GitCommitView(branch=branch, commit=sha)


@router.post("/notebooks/promote", response_model=PromoteView)
async def v1_notebook_promote(
    request: Request, env: Environment, path: str, payload: PromoteRequest
) -> PromoteView:
    """Preview (or, with ``apply``, write) a dbt model made from a Trino SQL cell."""
    target = _target(request, env, allowed_query=_PATH_QUERY)
    document = _read(path)
    cell = next((item for item in document.cells if item.id == payload.cell_id), None)
    if cell is None or cell.kind != "sql" or cell.engine == "duckdb":
        raise UnprocessableInputError(
            "Only a Trino SQL cell can become a model.", code="notebook_invalid"
        )
    try:
        catalog = preview_catalog(env, target.nessie_ref)
    except PreviewUnavailable as exc:
        raise BackendUnavailableError(str(exc)) from exc
    try:
        planned = notebook_promote.plan(
            project_root(),
            notebook=path,
            cell_name=cell.name,
            sql=cell.source,
            model=payload.model,
            catalog=catalog,
        )
        if payload.apply:
            notebook_promote.apply(project_root(), planned)
    except notebook_promote.PromoteInvalid as exc:
        raise UnprocessableInputError(str(exc), code="notebook_invalid") from exc
    except notebook_promote.PromoteConflict as exc:
        raise ConflictError(str(exc), code="notebook_model_exists") from exc
    if payload.apply:
        _audit(
            request,
            "notebook.promote",
            f"{env}:{path}",
            {"cell": cell.id, "model": payload.model},
            "written",
        )
    return PromoteView(
        files=[PromotedFile(**vars(item)) for item in planned], applied=payload.apply
    )


# -- kernels --------------------------------------------------------------------


@router.get("/notebooks/kernels", response_model=list[KernelView])
async def v1_notebook_kernels(request: Request, env: Environment) -> list[KernelView]:
    _target(request, env)
    _require_kernels()
    try:
        records = REGISTRY.for_actor(_actor(request))
    except KernelsUnavailable as exc:
        raise BackendUnavailableError(str(exc), code="notebook_kernels_unavailable") from exc
    return [_kernel_view(record) for record in records if record.env == env]


@router.post("/notebooks/kernels", response_model=KernelView, status_code=201)
async def v1_notebook_kernel_start(
    request: Request, env: Environment, payload: KernelStart
) -> KernelView:
    """Start this person's kernel for a notebook, with the packages its file lists."""
    _target(request, env)
    deployment = _require_kernels()
    document = _read(payload.path)
    try:
        record = await REGISTRY.start(
            actor=_actor(request),
            env=env,
            path=payload.path,
            packages=list(document.settings.packages),
            allow_packages=deployment.notebook_packages,
        )
    except KernelsUnavailable as exc:
        raise BackendUnavailableError(str(exc), code="notebook_kernels_unavailable") from exc
    except KernelLimitReached as exc:
        raise ConflictError(str(exc), code="notebook_kernel_limit") from exc
    except PackagesDisabled as exc:
        raise ConflictError(str(exc), code="notebook_packages_disabled") from exc
    except PackagesInvalid as exc:
        raise UnprocessableInputError(str(exc), code="notebook_invalid") from exc
    _audit(
        request,
        "notebook.kernel.start",
        f"{env}:{payload.path}",
        {"kernel": record.id, "packages": record.packages},
        record.state,
    )
    return _kernel_view(record)


def _work(payload: RunStart) -> CellWork:
    if payload.kind != "python" and not payload.name:
        raise UnprocessableInputError("SQL cells need a result name.", code="notebook_invalid")
    if payload.kind != "python" and not payload.source.strip():
        raise UnprocessableInputError("Write some SQL first.", code="notebook_invalid")
    return CellWork(payload.kind, payload.source, payload.name)


@router.post("/notebooks/kernels/{kernel_id}/runs", response_model=RunView, status_code=202)
async def v1_notebook_kernel_run(
    request: Request, env: Environment, kernel_id: str, payload: RunStart
) -> RunView:
    """Start running a cell. Poll the run for output; it keeps going if the page leaves."""
    record = _kernel(request, env, kernel_id)
    deployment = _deployment()
    work = _work(payload)
    if work.kind == "trino":
        _require_query(request, env)
        target = _targets()[env]
        try:
            validate_workspace_query(work.source, preview_catalog(env, target.nessie_ref))
        except PreviewUnavailable as exc:
            raise BackendUnavailableError(
                "The lakehouse query engine is not set up for this environment."
            ) from exc
        except InvalidWorkspaceQuery as exc:
            raise UnprocessableInputError(str(exc), code="notebook_invalid_query") from exc
    project, actor = project_root(), _actor(request)
    path = payload.path or record.path
    trino = trino_rows(deployment)

    async def body(progress: Callable[[ExecutionResult], None]) -> ExecutionResult:
        try:
            return await run_cell(
                REGISTRY,
                record,
                work,
                env=env,
                trino=trino,
                timeout=float(deployment.notebook_cell_timeout),
                progress=progress,
            )
        except PreviewQueryRejected as exc:
            return _error(str(exc), exc.error_name or "QueryRejected")
        except PreviewLimitExceeded as exc:
            return _error(
                f"{exc} Narrow the query, or ask an operator to raise PHLO_NOTEBOOK_MAX_ROWS.",
                "QueryLimit",
            )
        except PreviewUnavailable as exc:
            return _error(str(exc), "QueryUnavailable")

    async def keep(run: RunRecord) -> None:
        if payload.cell_id and run.result is not None and run.status != "cancelled":
            state.record_output(
                project,
                path,
                payload.cell_id,
                source_sha256=state.source_hash(payload.source),
                actor=actor,
                run=run.result,
                retention_days=deployment.notebook_output_days,
            )

    try:
        run = REGISTRY.start_run(record, work.kind, body, on_finish=keep)
    except KernelBusy as exc:
        raise ConflictError(str(exc), code="notebook_kernel_busy") from exc
    except KernelNotReady as exc:
        raise ConflictError(str(exc), code="notebook_kernel_starting") from exc
    _audit(
        request,
        f"notebook.cell.{work.kind}",
        f"{env}:{record.path}",
        {"kernel": kernel_id, "run": run.id, "source_sha256": _sha(payload.source)},
        "started",
    )
    return _run_view(run)


def _error(message: str, name: str) -> ExecutionResult:
    return ExecutionResult("error", None, 0, [KernelOutput(type="error", ename=name, text=message)])


@router.get("/notebooks/kernels/{kernel_id}/runs/{run_id}", response_model=RunView)
async def v1_notebook_kernel_run_status(
    request: Request, env: Environment, kernel_id: str, run_id: str
) -> RunView:
    record = _kernel(request, env, kernel_id)
    try:
        return _run_view(REGISTRY.get_run(record, run_id))
    except KernelNotFound as exc:
        raise NotFoundError("That run is no longer available.") from exc


def _busy() -> ConflictError:
    return ConflictError("The kernel is still running a cell.", code="notebook_kernel_busy")


@router.get("/notebooks/kernels/{kernel_id}/variables", response_model=KernelVariables)
async def v1_notebook_kernel_variables(
    request: Request, env: Environment, kernel_id: str
) -> KernelVariables:
    record = _kernel(request, env, kernel_id)
    try:
        items = await REGISTRY.variables(record)
    except (KernelBusy, KernelNotReady) as exc:
        raise _busy() from exc
    return KernelVariables(items=[KernelVariable.model_validate(item) for item in items])


@router.get("/notebooks/kernels/{kernel_id}/packages", response_model=KernelPackages)
async def v1_notebook_kernel_packages(
    request: Request, env: Environment, kernel_id: str
) -> KernelPackages:
    """Every importable distribution in the kernel, marking the notebook's own."""
    record = _kernel(request, env, kernel_id)
    try:
        items = await REGISTRY.packages(record)
    except (KernelBusy, KernelNotReady) as exc:
        raise _busy() from exc
    return KernelPackages(
        requested=record.packages, items=[KernelPackage.model_validate(item) for item in items]
    )


@router.post("/notebooks/kernels/{kernel_id}/complete", response_model=Completions)
async def v1_notebook_kernel_complete(
    request: Request, env: Environment, kernel_id: str, payload: CompleteRequest
) -> Completions:
    """Python completions from the live kernel; empty while it runs a cell."""
    record = _kernel(request, env, kernel_id)
    cursor = min(payload.cursor, len(payload.code))
    return Completions.model_validate(await REGISTRY.complete(record, payload.code, cursor))


@router.post("/notebooks/kernels/{kernel_id}/interrupt", response_model=KernelView)
async def v1_notebook_kernel_interrupt(
    request: Request, env: Environment, kernel_id: str
) -> KernelView:
    record = _kernel(request, env, kernel_id)
    REGISTRY.interrupt(record)
    _audit(
        request,
        "notebook.kernel.interrupt",
        f"{env}:{record.path}",
        {"kernel": kernel_id},
        "interrupted",
    )
    return _kernel_view(record)


@router.post("/notebooks/kernels/{kernel_id}/restart", response_model=KernelView)
async def v1_notebook_kernel_restart(
    request: Request, env: Environment, kernel_id: str
) -> KernelView:
    record = _kernel(request, env, kernel_id)
    try:
        record = await REGISTRY.restart(record)
    except Exception as exc:  # reason: a failed restart leaves a failed kernel to report
        raise BackendUnavailableError(f"The kernel did not restart: {exc}") from exc
    _audit(
        request,
        "notebook.kernel.restart",
        f"{env}:{record.path}",
        {"kernel": kernel_id},
        "restarted",
    )
    return _kernel_view(record)


@router.delete("/notebooks/kernels/{kernel_id}", status_code=204)
async def v1_notebook_kernel_stop(request: Request, env: Environment, kernel_id: str) -> Response:
    record = _kernel(request, env, kernel_id)
    await REGISTRY.shutdown(record)
    _audit(
        request, "notebook.kernel.stop", f"{env}:{record.path}", {"kernel": kernel_id}, "stopped"
    )
    return Response(status_code=204)
