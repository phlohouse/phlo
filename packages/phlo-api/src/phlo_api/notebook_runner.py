"""Run notebook cells, whole notebooks headlessly, and notebooks on their cron schedules.

``run_cell`` is shared by Observatory's interactive runs and headless runs, so
a scheduled run does exactly what pressing Run all does: Trino SQL goes through
the validated read-only query path and is bound into the kernel, DuckDB SQL and
Python run inside the kernel.

The scheduler is opt-in (``PHLO_NOTEBOOK_SCHEDULES=1``) and holds a lock file
while it runs, so a reloading API never fires a schedule twice. It never backfills: a notebook missed while the API was down runs at
its next due time, and a due time older than an hour is skipped.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from phlo_api import notebook_files as files
from phlo_api import notebook_signing as signing
from phlo_api import notebook_state as state
from phlo_api.notebook_kernels import (
    ExecutionResult,
    KernelOutput,
    KernelRecord,
    KernelRegistry,
    _result_dict,
    _try_lock,
)

logger = logging.getLogger(__name__)

# (env, sql, cancelled) -> {"columns": [...], "rows": [...], "has_more": bool}
TrinoRows = Callable[[str, str, Callable[[], bool]], Awaitable[dict[str, Any]]]

_CATCH_UP = timedelta(hours=1)
SCHEDULE_ACTOR = "notebook-schedule:"


@dataclass(frozen=True)
class CellWork:
    kind: str  # "python" | "duckdb" | "trino"
    source: str
    name: str | None


def cell_work(cell: files.NotebookCell) -> CellWork | None:
    if cell.kind == "markdown" or not cell.source.strip():
        return None
    if cell.kind == "sql":
        return CellWork("duckdb" if cell.engine == "duckdb" else "trino", cell.source, cell.name)
    return CellWork("python", cell.source, cell.name)


async def run_cell(
    registry: KernelRegistry,
    record: KernelRecord,
    work: CellWork,
    *,
    env: str,
    trino: TrinoRows,
    timeout: float,
    progress: Callable[[ExecutionResult], None] | None = None,
) -> ExecutionResult:
    """Run one cell; the caller holds the kernel's busy lock."""
    if work.kind == "python":
        return await registry.execute(
            record, work.source, preview=work.name, timeout=timeout, progress=progress
        )
    if not work.name:
        return _failed("SQL cells need a result name.")
    if work.kind == "duckdb":
        return await registry.duckdb(
            record, work.name, work.source, timeout=timeout, progress=progress
        )
    started = time.monotonic()
    try:
        rows = await asyncio.wait_for(
            trino(env, work.source, lambda: registry.cancelled(record)), timeout
        )
    except TimeoutError:
        return ExecutionResult("timeout", None, int((time.monotonic() - started) * 1000), [])
    bound = await registry.bind_rows(record, work.name, rows)
    bound.duration_ms = int((time.monotonic() - started) * 1000)
    if rows.get("has_more"):
        bound.truncated = True
        bound.outputs.append(
            KernelOutput(
                type="stream",
                name="stderr",
                text=f"Only the first {len(rows['rows']):,} rows were loaded into {work.name}.\n",
            )
        )
    return bound


def _failed(message: str, name: str = "NotebookError") -> ExecutionResult:
    return ExecutionResult("error", None, 0, [KernelOutput(type="error", ename=name, text=message)])


async def run_notebook(
    registry: KernelRegistry,
    *,
    project: Path,
    path: str,
    env: str,
    actor: str,
    trigger: str,
    trino: TrinoRows,
    timeout: float,
    output_days: int,
    allow_packages: bool,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run every cell in order in a fresh kernel, stopping at the first failure."""
    run: dict[str, Any] = {
        "id": run_id or uuid4().hex,
        "trigger": trigger,
        "actor": actor,
        "env": env,
        "status": "running",
        "started_at": time.time(),
        "finished_at": None,
        "message": None,
        "cells": [],
    }
    state.record_run(project, path, run)
    record: KernelRecord | None = None
    try:
        document = files.read_notebook(project, path)
        kernel_actor = actor if trigger == "manual" else f"{SCHEDULE_ACTOR}{actor}"
        record = await registry.start(
            actor=f"{kernel_actor}#run",
            env=env,
            path=path,
            packages=list(document.settings.packages),
            allow_packages=allow_packages,
        )
        record = await _wait_ready(registry, record)
        await _run_cells(registry, record, document, run, env=env, trino=trino, timeout=timeout)
        run["status"] = "failed" if any(c["status"] != "ok" for c in run["cells"]) else "ok"
        _keep_outputs(project, path, run, actor, output_days)
    except Exception as exc:  # reason: every failure ends the run with a reason
        run["status"] = "failed"
        run["message"] = str(exc)[:2000] or type(exc).__name__
    finally:
        run["finished_at"] = time.time()
        for cell in run["cells"]:
            cell.pop("_output", None)
            cell.pop("_source", None)
        if record is not None:
            await registry.shutdown(record)
        state.record_run(project, path, run)
    return run


async def _wait_ready(registry: KernelRegistry, record: KernelRecord) -> KernelRecord:
    deadline = time.monotonic() + 1800
    while record.state in {"installing", "starting"} and time.monotonic() < deadline:
        await asyncio.sleep(1)
        fresh = await registry.get(record.id, record.actor)
        record = fresh
    if record.state != "ready":
        raise RuntimeError(record.message or "The kernel did not start.")
    return record


async def _run_cells(
    registry: KernelRegistry,
    record: KernelRecord,
    document: files.NotebookDocument,
    run: dict[str, Any],
    *,
    env: str,
    trino: TrinoRows,
    timeout: float,
) -> None:
    with registry.busy(record):
        for cell in document.cells:
            work = cell_work(cell)
            if work is None:
                continue
            try:
                result = await run_cell(
                    registry, record, work, env=env, trino=trino, timeout=timeout
                )
            except Exception as exc:  # reason: a failing cell is recorded, not raised
                result = _failed(str(exc), type(exc).__name__)
            run["cells"].append(
                {
                    "cell_id": cell.id,
                    "status": result.status,
                    "duration_ms": result.duration_ms,
                    "error": next((o.ename for o in result.outputs if o.type == "error"), None),
                    "_output": _result_dict(result),
                    "_source": cell.source,
                }
            )
            if result.status != "ok":
                break


def _keep_outputs(
    project: Path,
    path: str,
    run: dict[str, Any],
    actor: str,
    output_days: int,
) -> None:
    for cell in run["cells"]:
        output = cell.pop("_output", None)
        source = cell.pop("_source", "")
        if output is not None:
            state.record_output(
                project,
                path,
                cell["cell_id"],
                source_sha256=state.source_hash(source),
                actor=actor,
                run=output,
                retention_days=output_days,
            )


def next_due(cron: str, after: datetime) -> datetime | None:
    try:
        from croniter import croniter
    except ImportError:
        return None
    return croniter(cron, after).get_next(datetime)


def previous_due(cron: str, now: datetime) -> datetime | None:
    try:
        from croniter import croniter
    except ImportError:
        return None
    return croniter(cron, now).get_prev(datetime)


class NotebookScheduler:
    """Fires saved notebook schedules while it holds the scheduler lock."""

    def __init__(
        self,
        registry: KernelRegistry,
        *,
        project: Callable[[], Path],
        trino: TrinoRows,
        timeout: float,
        output_days: int,
        allow_packages: bool,
        envs: Callable[[], set[str]],
        schedule_key: Callable[[], str | None],
        interval: float = 30.0,
    ) -> None:
        self._registry = registry
        self._project = project
        self._trino = trino
        self._timeout = timeout
        self._output_days = output_days
        self._allow_packages = allow_packages
        self._envs = envs
        self._schedule_key = schedule_key
        self._interval = interval
        self._task: asyncio.Task[None] | None = None
        # The due time each notebook last fired for, so one slot runs once.
        self._fired: dict[str, float] = {}
        self._lock: int | None = None

    def start(self) -> None:
        self._task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None

    async def _loop(self) -> None:
        while True:
            try:
                if self._lead():
                    await self.tick(datetime.now(UTC))
            except asyncio.CancelledError:
                raise
            except Exception:  # reason: one bad tick must not stop future schedules
                logger.exception("notebook_scheduler_tick_failed")
            await asyncio.sleep(self._interval)

    def _lead(self) -> bool:
        if self._lock is None:
            self._lock = _try_lock(self._registry.root / "scheduler.lock")
        return self._lock is not None

    async def tick(self, now: datetime) -> list[str]:
        """Run every notebook whose schedule fell due since its last scheduled run."""
        project = self._project()
        started: list[str] = []
        items, _ = files.list_notebooks(project)
        envs = self._envs()
        for item in items:
            try:
                document = files.read_notebook(project, item.path)
            except (files.NotebookInvalid, files.NotebookPathError, OSError):
                continue
            schedule = document.settings.schedule
            if schedule is None or not schedule.enabled or schedule.env not in envs:
                continue
            due = previous_due(schedule.cron, now)
            if due is None or now - due > _CATCH_UP:
                continue
            if schedule.since is not None and due.timestamp() < schedule.since:
                continue  # that slot came before the schedule was set
            last = next(
                (
                    run
                    for run in state.read_runs(project, item.path)
                    if run.get("trigger") == "schedule"
                ),
                None,
            )
            if last is not None and float(last.get("started_at") or 0) >= due.timestamp():
                continue
            if self._fired.get(item.path) == due.timestamp():
                continue
            self._fired[item.path] = due.timestamp()
            started.append(item.path)
            refusal = _refusal(self._schedule_key(), document)
            if refusal is not None:
                _record_refused(project, item.path, schedule, refusal)
                continue
            await run_notebook(
                self._registry,
                project=project,
                path=item.path,
                env=schedule.env,
                actor=schedule.owner,
                trigger="schedule",
                trino=self._trino,
                timeout=self._timeout,
                output_days=self._output_days,
                allow_packages=self._allow_packages,
            )
        return started


def _refusal(key: str | None, document: files.NotebookDocument) -> str | None:
    """Why a due schedule must not run, or ``None`` when it may."""
    if key is None:
        return "Schedules are unavailable: PHLO_NOTEBOOK_SCHEDULE_KEY is not set."
    if not signing.verify(key, document):
        return (
            "This schedule is paused: the notebook changed since it was scheduled, or the "
            "schedule was not set from Observatory. Set the schedule again to resume it."
        )
    schedule = document.settings.schedule
    if schedule is not None and signing.needs_query(document) and not schedule.query:
        return "This schedule may not query the lakehouse. Set the schedule again."
    return None


def _record_refused(
    project: Path, path: str, schedule: files.NotebookSchedule, message: str
) -> None:
    now = time.time()
    state.record_run(
        project,
        path,
        {
            "id": uuid4().hex,
            "trigger": "schedule",
            "actor": schedule.owner,
            "env": schedule.env,
            "status": "failed",
            "started_at": now,
            "finished_at": now,
            "message": message,
            "cells": [],
        },
    )
