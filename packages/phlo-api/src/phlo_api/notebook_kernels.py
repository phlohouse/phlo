"""Process-local Jupyter kernels for Observatory notebooks.

Each kernel belongs to one signed-in person and one notebook. Kernels start
with a scrubbed environment, so API secrets (Trino passwords, tokens) never
reach notebook code. Lakehouse data enters a kernel only as rows from a
completed, validated query session, bound under a name the person chose.

Kernel output is returned as plain text, PNG images and structured table
previews. HTML output is dropped on purpose: Observatory renders tables with
its own components, never markup produced by notebook code.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

MAX_KERNELS = 8
MAX_KERNELS_PER_ACTOR = 2
IDLE_SECONDS = 30 * 60
EXECUTE_TIMEOUT_SECONDS = 120.0
MAX_OUTPUT_CHARS = 200_000
PREVIEW_ROWS = 100
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_MARKER = "__phlo_json__:"
_ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT")

# Runs once in every new or restarted kernel. Helpers are underscored so the
# Variables panel hides them.
_PREAMBLE = r"""
import json as __phlo_json

def __phlo_frame(payload):
    data = __phlo_json.loads(payload)
    columns = [column["name"] for column in data["columns"]]
    rows = [[row.get(name) for name in columns] for row in data["rows"]]
    try:
        import polars as __pl
        return __pl.DataFrame(rows, schema=columns, orient="row")
    except ImportError:
        import pandas as __pd
        return __pd.DataFrame.from_records(rows, columns=columns)

def __phlo_sql(sql):
    import duckdb as __duckdb
    relation = __duckdb.sql(sql)
    try:
        import polars  # noqa: F401
        return relation.pl()
    except ImportError:
        return relation.df()

def __phlo_preview(name, limit):
    value = globals().get(name)
    out = {"name": name, "found": value is not None}
    if value is None:
        print("__phlo_json__:" + __phlo_json.dumps(out))
        return
    shape = getattr(value, "shape", None)
    out["type"] = type(value).__module__.split(".")[0] + "." + type(value).__name__
    if shape is not None and hasattr(value, "columns") and len(shape) == 2:
        head = value.head(limit)
        if hasattr(head, "to_dicts"):
            rows = head.to_dicts()
        else:
            rows = head.to_dict(orient="records")
        out["table"] = {
            "columns": [{"name": str(c), "type": str(t)} for c, t in zip(value.columns, getattr(value, "dtypes", []))],
            "rows": __phlo_json.loads(__phlo_json.dumps(rows, default=str)),
            "row_count": int(shape[0]),
        }
    print("__phlo_json__:" + __phlo_json.dumps(out, default=str))

def __phlo_variables():
    import types as __types
    items = []
    for key, value in list(globals().items()):
        if key.startswith("_") or key in {"In", "Out", "get_ipython", "exit", "quit"}:
            continue
        if isinstance(value, (__types.ModuleType, __types.FunctionType, type)):
            continue
        shape = getattr(value, "shape", None)
        columns = getattr(value, "columns", None)
        preview = ", ".join(str(c) for c in list(columns)[:8]) if columns is not None else repr(value)[:120]
        items.append({
            "name": key,
            "type": type(value).__module__.split(".")[0] + "." + type(value).__name__,
            "shape": " × ".join(str(n) for n in shape) if shape is not None else None,
            "preview": preview,
        })
    print("__phlo_json__:" + __phlo_json.dumps(items[:200], default=str))
"""


class KernelsUnavailable(RuntimeError):
    """Jupyter is not installed in the API environment."""


class KernelLimitReached(RuntimeError):
    """Too many kernels are running."""


class KernelNotFound(LookupError):
    """No kernel with that id belongs to this person."""


class KernelBusy(RuntimeError):
    """The kernel is still running another cell."""


@dataclass
class KernelOutput:
    type: Literal["stream", "result", "image", "error"]
    text: str | None = None
    name: str | None = None
    png: str | None = None
    ename: str | None = None


@dataclass
class ExecutionResult:
    status: Literal["ok", "error", "timeout"]
    execution_count: int | None
    duration_ms: int
    outputs: list[KernelOutput]
    truncated: bool = False
    table: dict[str, Any] | None = None


@dataclass
class KernelSession:
    id: str
    actor: str
    env: str
    path: str
    manager: Any
    client: Any
    workdir: str
    started_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    execution_count: int = 0

    @property
    def status(self) -> Literal["idle", "busy"]:
        return "busy" if self.lock.locked() else "idle"


def _jupyter() -> Any:
    try:
        from jupyter_client.manager import AsyncKernelManager
    except ImportError as exc:  # pragma: no cover - exercised when the extra is absent
        raise KernelsUnavailable(
            "Notebook kernels need the phlo-api 'notebooks' extra (jupyter-client and ipykernel)."
        ) from exc
    return AsyncKernelManager


def kernel_environment() -> dict[str, str]:
    """Only locale and PATH reach a kernel; credentials in the API process stay there."""
    env = {key: os.environ[key] for key in _ENV_KEEP if key in os.environ}
    env["PYTHONUNBUFFERED"] = "1"
    env["MPLBACKEND"] = "Agg"
    return env


def _clean(text: str) -> str:
    return _ANSI.sub("", text)


class KernelRegistry:
    """Owns every running kernel in this API process."""

    def __init__(self) -> None:
        self._kernels: dict[str, KernelSession] = {}
        self._starting = asyncio.Lock()

    def get(self, kernel_id: str, actor: str) -> KernelSession:
        session = self._kernels.get(kernel_id)
        if session is None or session.actor != actor:
            raise KernelNotFound(kernel_id)
        return session

    def for_actor(self, actor: str) -> list[KernelSession]:
        return [k for k in self._kernels.values() if k.actor == actor]

    async def prune(self) -> None:
        cutoff = time.time() - IDLE_SECONDS
        for session in list(self._kernels.values()):
            if session.last_used < cutoff and not session.lock.locked():
                await self.shutdown(session)

    async def start(self, *, actor: str, env: str, path: str) -> KernelSession:
        manager_type = _jupyter()
        async with self._starting:
            await self.prune()
            for existing in self.for_actor(actor):
                if existing.path == path and existing.env == env:
                    existing.last_used = time.time()
                    return existing
            if len(self._kernels) >= MAX_KERNELS:
                raise KernelLimitReached("All notebook kernels are in use. Try again shortly.")
            if len(self.for_actor(actor)) >= MAX_KERNELS_PER_ACTOR:
                raise KernelLimitReached(
                    f"You already have {MAX_KERNELS_PER_ACTOR} notebook kernels running. Stop one first."
                )
            workdir = tempfile.mkdtemp(prefix="phlo-kernel-")
            # IPC sockets in the kernel's own folder: nothing listens on TCP.
            manager = manager_type(
                kernel_name="python3",
                transport="ipc",
                ip=os.path.join(workdir, "kernel"),
                connection_file=os.path.join(workdir, "kernel.json"),
            )
            await manager.start_kernel(env=kernel_environment(), cwd=workdir)
            client = manager.client()
            client.start_channels()
            try:
                await client.wait_for_ready(timeout=60)
            except Exception:
                client.stop_channels()
                await manager.shutdown_kernel(now=True)
                shutil.rmtree(workdir, ignore_errors=True)
                raise
            session = KernelSession(
                id=uuid4().hex,
                actor=actor,
                env=env,
                path=path,
                manager=manager,
                client=client,
                workdir=workdir,
            )
            self._kernels[session.id] = session
        await self._run(session, _PREAMBLE, silent=True)
        return session

    async def shutdown(self, session: KernelSession) -> None:
        self._kernels.pop(session.id, None)
        try:
            session.client.stop_channels()
        finally:
            try:
                await session.manager.shutdown_kernel(now=True)
            finally:
                shutil.rmtree(session.workdir, ignore_errors=True)

    async def shutdown_all(self) -> None:
        for session in list(self._kernels.values()):
            await self.shutdown(session)

    async def interrupt(self, session: KernelSession) -> None:
        await session.manager.interrupt_kernel()

    async def restart(self, session: KernelSession) -> None:
        async with session.lock:
            await session.manager.restart_kernel(now=True)
            await session.client.wait_for_ready(timeout=60)
            session.execution_count = 0
        await self._run(session, _PREAMBLE, silent=True)

    async def execute(
        self, session: KernelSession, code: str, *, preview: str | None = None
    ) -> ExecutionResult:
        if session.lock.locked():
            raise KernelBusy("The kernel is still running another cell.")
        result = await self._run(session, code)
        if preview and result.status == "ok":
            result.table = await self._preview(session, preview)
        return result

    async def bind_rows(
        self, session: KernelSession, name: str, result: dict[str, Any]
    ) -> ExecutionResult:
        """Bind a completed query result into the kernel as a data frame called ``name``."""
        if not _NAME.fullmatch(name):
            raise ValueError("Result names are Python identifiers.")
        payload = json.dumps({"columns": result["columns"], "rows": result["rows"]}, default=str)
        code = f"{name} = __phlo_frame({json.dumps(payload)})"
        run = await self._run(session, code, silent=True)
        if run.status == "ok":
            run.table = await self._preview(session, name)
        return run

    async def duckdb(self, session: KernelSession, name: str, sql: str) -> ExecutionResult:
        """Run SQL over data frames already in the kernel, in-process with DuckDB."""
        if not _NAME.fullmatch(name):
            raise ValueError("Result names are Python identifiers.")
        return await self.execute(session, f"{name} = __phlo_sql({json.dumps(sql)})", preview=name)

    async def variables(self, session: KernelSession) -> list[dict[str, Any]]:
        run = await self._run(session, "__phlo_variables()", silent=True)
        return self._marker(run) or []

    async def _preview(self, session: KernelSession, name: str) -> dict[str, Any] | None:
        run = await self._run(
            session, f"__phlo_preview({json.dumps(name)}, {PREVIEW_ROWS})", silent=True
        )
        data = self._marker(run)
        return data.get("table") if isinstance(data, dict) else None

    @staticmethod
    def _marker(run: ExecutionResult) -> Any:
        for output in run.outputs:
            if output.type == "stream" and output.text:
                for line in output.text.splitlines():
                    if line.startswith(_MARKER):
                        return json.loads(line[len(_MARKER) :])
        return None

    async def _run(
        self, session: KernelSession, code: str, *, silent: bool = False
    ) -> ExecutionResult:
        async with session.lock:
            session.last_used = time.time()
            started = time.monotonic()
            client = session.client
            msg_id = client.execute(code, store_history=not silent, allow_stdin=False)
            outputs: list[KernelOutput] = []
            size = 0
            truncated = False
            status: Literal["ok", "error", "timeout"] = "ok"
            count: int | None = None
            deadline = started + EXECUTE_TIMEOUT_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    await session.manager.interrupt_kernel()
                    status = "timeout"
                    break
                try:
                    message = await client.get_iopub_msg(timeout=remaining)
                except Exception:
                    continue
                if message.get("parent_header", {}).get("msg_id") != msg_id:
                    continue
                kind = message["msg_type"]
                content = message["content"]
                if kind == "status" and content.get("execution_state") == "idle":
                    break
                if kind == "execute_input":
                    count = content.get("execution_count")
                    continue
                output = _to_output(kind, content)
                if output is None:
                    continue
                if output.type == "error":
                    status = "error"
                length = len(output.text or "") + len(output.png or "")
                if size + length > MAX_OUTPUT_CHARS:
                    truncated = True
                    continue
                size += length
                outputs.append(output)
            if not silent and count is not None:
                session.execution_count = count
            return ExecutionResult(
                status=status,
                execution_count=None if silent else count,
                duration_ms=int((time.monotonic() - started) * 1000),
                outputs=outputs,
                truncated=truncated,
            )


def _to_output(kind: str, content: dict[str, Any]) -> KernelOutput | None:
    if kind == "stream":
        return KernelOutput(
            type="stream", name=content.get("name"), text=_clean(content.get("text", ""))
        )
    if kind == "error":
        trace = "\n".join(_clean(line) for line in content.get("traceback", []))
        return KernelOutput(
            type="error",
            ename=content.get("ename"),
            text=trace or f"{content.get('ename')}: {content.get('evalue')}",
        )
    if kind in {"execute_result", "display_data"}:
        data = content.get("data", {})
        if "image/png" in data:
            return KernelOutput(type="image", png=data["image/png"])
        if "text/plain" in data:
            return KernelOutput(type="result", text=_clean(data["text/plain"]))
    return None


REGISTRY = KernelRegistry()
