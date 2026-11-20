"""Observatory notebook kernels, run by the separate notebook kernel host.

Each kernel belongs to one signed-in person and one notebook. The kernels
themselves run in the ``phlo-notebook-kernels`` container (see
:mod:`phlo_api.notebook_gateway`), each as its own Unix user with no network
access, so notebook code never shares a process, a user or a network with
the API, never sees API credentials and cannot reach another person's kernel.
Lakehouse data enters a kernel only as rows from a validated read-only query,
bound under a name the person chose and sent through the host.

The API keeps its own records of kernels and background runs in a private
folder (``PHLO_NOTEBOOK_KERNEL_DIR``) and talks to the host over HTTP with a
bearer token (``PHLO_NOTEBOOK_GATEWAY_URL``, ``PHLO_NOTEBOOK_GATEWAY_TOKEN``).
The host holds each kernel's signing key; the API never does. The API runs as
one process. Cells run as background runs whose output is written to the
kernel's folder as it arrives, so a long cell never holds an HTTP request open.

Kernel output is plain text, PNG images and structured table previews. HTML
output is dropped on purpose: Observatory renders tables with its own
components, never markup produced by notebook code.
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

_fcntl: Any = None
try:
    import fcntl as _fcntl
except ImportError:
    pass  # Windows: kernels are reported as unavailable.

MAX_KERNELS = 8
MAX_KERNELS_PER_ACTOR = 2
IDLE_SECONDS = 30 * 60
READY_SECONDS = 60.0
MAX_OUTPUT_CHARS = 400_000
PREVIEW_ROWS = 100
MAX_PACKAGES = 50
RUN_KEEP_SECONDS = 15 * 60
RUN_LOCK_WAIT_SECONDS = 3.0
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_ID = re.compile(r"^[a-f0-9]{16,32}$")
_MARKER = "__phlo_json__:"
INSTALL_SECONDS = 900.0
GATEWAY_SECONDS = 30.0

# Runs once in every new or restarted kernel. Helpers are underscored so the
# Variables panel hides them. The kernel host stops kernels left idle.
_PREAMBLE = r"""
import json as __phlo_json

def __phlo_frame(payload):
    data = __phlo_json.loads(payload)
    columns = [column["name"] for column in data["columns"]]
    rows = [[row.get(name) for name in columns] for row in data["rows"]]
    # Rows arrive as JSON, so dates, timestamps and decimals are text; the
    # Trino column types turn them back into real values.
    kinds = {
        column["name"]: (column.get("type") or "").lower()
        for column in data["columns"]
        if columns.count(column["name"]) == 1
    }
    try:
        import polars as __pl
        frame = __pl.DataFrame(rows, schema=columns, orient="row")
        casts = []
        for name, kind in kinds.items():
            text = __pl.col(name).cast(__pl.Utf8)
            if kind == "date":
                casts.append(text.str.to_date(strict=False))
            elif kind.startswith("timestamp"):
                casts.append(text.str.to_datetime(strict=False))
            elif kind.startswith("decimal"):
                casts.append(__pl.col(name).cast(__pl.Float64, strict=False))
        return frame.with_columns(casts) if casts else frame
    except ImportError:
        import pandas as __pd
        frame = __pd.DataFrame.from_records(rows, columns=columns)
        for name, kind in kinds.items():
            if kind == "date" or kind.startswith("timestamp"):
                frame[name] = __pd.to_datetime(
                    frame[name], errors="coerce", utc="with time zone" in kind
                )
            elif kind.startswith("decimal"):
                frame[name] = __pd.to_numeric(frame[name], errors="coerce")
        return frame

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
            head = head.copy()
            for column in head.columns:
                # A Trino date becomes a pandas timestamp at midnight; show it as a date.
                try:
                    series = head[column]
                    known = series.dropna()
                    if str(series.dtype).startswith("datetime64") and (known == known.dt.normalize()).all():
                        head[column] = series.dt.strftime("%Y-%m-%d")
                except (AttributeError, TypeError, ValueError):
                    pass
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

def __phlo_packages(env_site):
    import importlib.metadata as __md, os
    items = {}
    for dist in __md.distributions():
        name = dist.metadata["Name"]
        if not name or name.lower() in items:
            continue
        location = str(getattr(dist, "_path", ""))
        source = "notebook" if env_site and location.startswith(env_site) else "base"
        items[name.lower()] = {"name": name, "version": dist.version, "source": source}
    print("__phlo_json__:" + __phlo_json.dumps(sorted(items.values(), key=lambda i: i["name"].lower())))
"""


class KernelsUnavailable(RuntimeError):
    """The kernel host is not configured or not reachable, or kernel state cannot be kept safely."""


class KernelLimitReached(RuntimeError):
    """Too many kernels are running."""


class KernelNotFound(LookupError):
    """No kernel with that id belongs to this person."""


class KernelBusy(RuntimeError):
    """The kernel is still running another cell."""


class KernelNotReady(RuntimeError):
    """The kernel is still starting or installing packages, or failed to start."""


class PackagesInvalid(ValueError):
    """A package requirement is not a plain name and version specifier."""


class PackagesDisabled(RuntimeError):
    """The operator has not allowed per-notebook packages."""


@dataclass
class KernelOutput:
    type: Literal["stream", "result", "image", "error"]
    text: str | None = None
    name: str | None = None
    png: str | None = None
    ename: str | None = None


RunStatus = Literal["running", "ok", "error", "timeout", "cancelled"]


@dataclass
class ExecutionResult:
    status: RunStatus
    execution_count: int | None
    duration_ms: int
    outputs: list[KernelOutput]
    truncated: bool = False
    table: dict[str, Any] | None = None


KernelState = Literal["installing", "starting", "ready", "failed"]


@dataclass
class KernelRecord:
    id: str
    actor: str
    env: str
    path: str
    packages: list[str] = field(default_factory=list)
    state: KernelState = "starting"
    message: str | None = None
    pid: int | None = None
    python: str | None = None
    site: str | None = None
    started_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    execution_count: int = 0


@dataclass
class RunRecord:
    id: str
    kernel: str
    kind: str
    status: RunStatus = "running"
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: dict[str, Any] | None = None


def normalize_packages(packages: list[str]) -> list[str]:
    """Accept only ``name[extras] <specifier>`` requirements: no URLs, paths or options."""
    from packaging.requirements import InvalidRequirement, Requirement

    if len(packages) > MAX_PACKAGES:
        raise PackagesInvalid(f"A notebook can list at most {MAX_PACKAGES} packages.")
    normalized: dict[str, str] = {}
    for raw in packages:
        text = raw.strip()
        if not text or text.startswith("-") or len(text) > 200:
            raise PackagesInvalid(f"'{raw}' is not a package requirement.")
        try:
            requirement = Requirement(text)
        except InvalidRequirement as exc:
            raise PackagesInvalid(f"'{raw}' is not a package requirement.") from exc
        if requirement.url:
            raise PackagesInvalid(f"'{raw}' points at a URL. Use a package name and version.")
        if requirement.marker is not None:
            raise PackagesInvalid(f"'{raw}' has an environment marker. Use a name and version.")
        normalized[requirement.name.lower()] = str(requirement)
    return [normalized[key] for key in sorted(normalized)]


def _lock_nb(descriptor: int) -> None:
    module = cast(Any, _fcntl)
    module.flock(descriptor, module.LOCK_EX | module.LOCK_NB)


def _clean(text: str) -> str:
    return _ANSI.sub("", text)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(handle, "w") as stream:
            json.dump(payload, stream, default=str)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


@contextmanager
def _flock(path: Path, *, wait: float = 10.0) -> Iterator[None]:
    """Hold an exclusive lock on ``path`` across processes, waiting up to ``wait`` seconds."""
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                _lock_nb(descriptor)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES) or time.monotonic() > deadline:
                    raise
                time.sleep(0.02)
        yield
    finally:
        os.close(descriptor)


@asynccontextmanager
async def _aflock(path: Path, *, wait: float = 10.0) -> AsyncIterator[None]:
    """``_flock`` for coroutines: waits without blocking the event loop."""
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                _lock_nb(descriptor)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES) or time.monotonic() > deadline:
                    raise
                await asyncio.sleep(0.02)
        yield
    finally:
        os.close(descriptor)


def _try_lock(path: Path) -> int | None:
    """Take a non-blocking exclusive lock, returning its descriptor or ``None`` when held."""
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        _lock_nb(descriptor)
    except OSError:
        os.close(descriptor)
        return None
    return descriptor


def default_kernel_root() -> Path:
    return Path(tempfile.gettempdir()) / f"phlo-kernels-{os.getuid()}"


class KernelRegistry:
    """Kernel records in a private folder owned by the API user; kernels on the kernel host."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        gateway_url: str | None = None,
        gateway_token: str | None = None,
    ) -> None:
        self._root = root
        self._gateway_url = gateway_url
        self._gateway_token = gateway_token
        self._tasks: set[asyncio.Task[Any]] = set()

    # -- folders -----------------------------------------------------------------

    @property
    def root(self) -> Path:
        if _fcntl is None or not hasattr(os, "getuid"):
            raise KernelsUnavailable("Notebook kernels need a POSIX host.")
        if self._root is None:
            self._root = default_kernel_root()
        root = self._root
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = root.lstat()
        if root.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise KernelsUnavailable(
                "The notebook kernel folder must be a private directory owned by the API user."
            )
        return root

    def configure(
        self, *, root: Path | None, gateway_url: str | None, gateway_token: str | None
    ) -> None:
        """Apply operator settings; the folder is created on first use."""
        if root is not None:
            self._root = root
        self._gateway_url = gateway_url
        self._gateway_token = gateway_token

    def _folder(self, kernel_id: str) -> Path:
        if not _ID.fullmatch(kernel_id):
            raise KernelNotFound(kernel_id)
        return self.root / kernel_id

    def _read(self, kernel_id: str) -> KernelRecord | None:
        try:
            raw = json.loads((self._folder(kernel_id) / "record.json").read_text())
            return KernelRecord(**raw)
        except (OSError, ValueError, TypeError, KernelNotFound):
            return None

    def _save(self, record: KernelRecord) -> None:
        _write_json(self._folder(record.id) / "record.json", asdict(record))

    def records(self) -> list[KernelRecord]:
        found = []
        for folder in sorted(self.root.iterdir()):
            if folder.is_dir() and _ID.fullmatch(folder.name):
                record = self._read(folder.name)
                if record is not None:
                    found.append(record)
        return found

    def touch(self, record: KernelRecord) -> None:
        record.last_used = time.time()

    # -- kernel host -------------------------------------------------------------

    def _gateway(self) -> tuple[str, dict[str, str]]:
        if not self._gateway_url or not self._gateway_token:
            raise KernelsUnavailable(
                "Notebook kernels run on the notebook kernel host. Start it with "
                "`phlo services start --profile notebooks` and set PHLO_NOTEBOOK_GATEWAY_URL "
                "and PHLO_NOTEBOOK_GATEWAY_TOKEN for the API."
            )
        return self._gateway_url.rstrip("/"), {"Authorization": f"Bearer {self._gateway_token}"}

    async def _host(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """One call to the kernel host; ``None`` when it does not know the kernel."""
        import httpx

        url, headers = self._gateway()
        try:
            async with httpx.AsyncClient(timeout=GATEWAY_SECONDS, trust_env=False) as http:
                response = await http.request(method, url + path, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise KernelsUnavailable(
                "The notebook kernel host is not reachable. Check that the "
                "phlo-notebook-kernels service is running."
            ) from exc
        if response.status_code == 404:
            return None
        if response.status_code == 401:
            raise KernelsUnavailable(
                "The notebook kernel host refused the API's token. "
                "PHLO_NOTEBOOK_GATEWAY_TOKEN must match on both."
            )
        if response.status_code == 204:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise KernelsUnavailable(
                "The notebook kernel host answered with invalid JSON."
            ) from exc
        if response.status_code == 409 and payload.get("error") == "kernel_limit":
            raise KernelLimitReached(payload.get("message") or "All notebook kernels are in use.")
        if response.status_code == 422:
            raise PackagesInvalid(payload.get("message") or "A package requirement is invalid.")
        if response.status_code >= 400:
            raise KernelsUnavailable(payload.get("message") or "The notebook kernel host failed.")
        return payload

    async def _host_state(self, record: KernelRecord) -> dict[str, Any] | None:
        return await self._host("GET", f"/kernels/{record.id}")

    # -- lookup ------------------------------------------------------------------

    async def get(self, kernel_id: str, actor: str) -> KernelRecord:
        record = self._read(kernel_id)
        if record is None or record.actor != actor:
            raise KernelNotFound(kernel_id)
        if record.state == "ready":
            remote = await self._host_state(record)
            if remote is None or remote.get("state") != "ready":
                self._remove(record)
                raise KernelNotFound(kernel_id)
        return record

    def for_actor(self, actor: str) -> list[KernelRecord]:
        return [record for record in self.records() if record.actor == actor]

    def status(
        self, record: KernelRecord
    ) -> Literal["installing", "starting", "failed", "idle", "busy"]:
        if record.state != "ready":
            return record.state
        descriptor = _try_lock(self._folder(record.id) / "busy.lock")
        if descriptor is None:
            return "busy"
        os.close(descriptor)
        return "idle"

    # -- lifecycle ---------------------------------------------------------------

    async def prune(self) -> None:
        """Forget kernels the host lost, stop idle ones and stop host kernels with no record."""
        cutoff = time.time() - IDLE_SECONDS
        listed = await self._host("GET", "/kernels") or {}
        remote = {item["id"]: item for item in listed.get("kernels", [])}
        known = set()
        for record in self.records():
            known.add(record.id)
            if record.state == "ready" and remote.get(record.id, {}).get("state") != "ready":
                await self._stop_remote(record)
                self._remove(record)
            elif record.state in {"installing", "starting"} and record.started_at < cutoff:
                await self._stop_remote(record)
                self._remove(record)
            elif record.last_used < cutoff and self.status(record) != "busy":
                await self._stop_remote(record)
                self._remove(record)
        for kernel_id in set(remote) - known:
            await self._host("DELETE", f"/kernels/{kernel_id}")

    async def start(
        self, *, actor: str, env: str, path: str, packages: list[str], allow_packages: bool
    ) -> KernelRecord:
        """Start (or reuse) this person's kernel for a notebook.

        Kernels without packages are ready when this returns. A kernel whose
        notebook lists packages comes back ``installing`` and becomes ready in
        the background once the host has built its environment.
        """
        packages = normalize_packages(packages)
        if packages and not allow_packages:
            raise PackagesDisabled(
                "This notebook lists Python packages, and an operator has not allowed them. "
                "Set PHLO_NOTEBOOK_PACKAGES=1, or remove the packages."
            )
        self._gateway()
        async with _aflock(self.root / "registry.lock"):
            await self.prune()
            for existing in self.for_actor(actor):
                if existing.path == path and existing.env == env:
                    if existing.packages == packages and existing.state != "failed":
                        self.touch(existing)
                        self._save(existing)
                        return existing
                    await self._stop_remote(existing)
                    self._remove(existing)
            records = self.records()
            if len(records) >= MAX_KERNELS:
                raise KernelLimitReached("All notebook kernels are in use. Try again shortly.")
            if sum(1 for r in records if r.actor == actor) >= MAX_KERNELS_PER_ACTOR:
                raise KernelLimitReached(
                    f"You already have {MAX_KERNELS_PER_ACTOR} notebook kernels running. Stop one first."
                )
            record = KernelRecord(id=uuid4().hex[:16], actor=actor, env=env, path=path)
            record.packages = packages
            folder = self.root / record.id
            folder.mkdir(mode=0o700)
            (folder / "runs").mkdir()
            remote = await self._host("POST", "/kernels", {"id": record.id, "packages": packages})
            record.state = (
                "installing" if (remote or {}).get("state") == "installing" else "starting"
            )
            self._save(record)
        if record.state == "installing":
            self.spawn(self._launch_quietly(record))
            return record
        await self._launch(record)
        return record

    def spawn(self, coroutine: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _launch_quietly(self, record: KernelRecord) -> None:
        with suppress(Exception):
            await self._launch(record)

    async def _launch(self, record: KernelRecord) -> None:
        """Wait for the host to report the kernel ready, then load the helpers into it."""
        deadline = time.monotonic() + INSTALL_SECONDS + READY_SECONDS
        try:
            while True:
                remote = await self._host_state(record)
                if remote is None:
                    raise RuntimeError("The kernel host lost the kernel.")
                if remote.get("state") == "ready":
                    record.site = remote.get("site")
                    break
                if remote.get("state") == "failed":
                    raise RuntimeError(remote.get("message") or "The kernel did not start.")
                if remote.get("state") == "starting" and record.state == "installing":
                    record.state = "starting"
                    self._save(record)
                if time.monotonic() > deadline:
                    raise TimeoutError("The kernel did not become ready in time.")
                await asyncio.sleep(0.25)
            async with self._client(record) as client:
                await self._execute(client, _PREAMBLE, silent=True, timeout=READY_SECONDS)
        except Exception as exc:
            with suppress(Exception):
                await self._stop_remote(record)
            record.state = "failed"
            record.message = f"The kernel did not start: {exc}"[:2000]
            if self._folder(record.id).exists():
                self._save(record)
            raise
        record.state = "ready"
        record.message = None
        record.execution_count = 0
        self.touch(record)
        self._save(record)

    async def _stop_remote(self, record: KernelRecord) -> None:
        with suppress(KernelsUnavailable):
            await self._host("DELETE", f"/kernels/{record.id}")

    def _remove(self, record: KernelRecord) -> None:
        shutil.rmtree(self.root / record.id, ignore_errors=True)

    async def shutdown(self, record: KernelRecord) -> None:
        await self._stop_remote(record)
        self._remove(record)

    async def shutdown_all(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._root is None or not self._root.exists():
            return
        for record in self.records():
            await self.shutdown(record)

    async def interrupt(self, record: KernelRecord) -> None:
        (self._folder(record.id) / "cancel").touch()
        with suppress(KernelsUnavailable):
            await self._host("POST", f"/kernels/{record.id}/interrupt")

    async def restart(self, record: KernelRecord) -> KernelRecord:
        descriptor = _try_lock(self._folder(record.id) / "busy.lock")
        if descriptor is None:
            await self.interrupt(record)
            await asyncio.sleep(0.5)
        else:
            os.close(descriptor)
        record.state = "starting"
        self._save(record)
        if await self._host("POST", f"/kernels/{record.id}/restart") is None:
            self._remove(record)
            raise KernelNotFound(record.id)
        await self._launch(record)
        return record

    # -- running code ------------------------------------------------------------

    @asynccontextmanager
    async def _client(self, record: KernelRecord) -> AsyncIterator[HostChannel]:
        url, headers = self._gateway()
        channel = HostChannel(url, record.id, headers)
        await channel.open()
        try:
            await channel.wait_for_ready(timeout=READY_SECONDS)
            yield channel
        finally:
            await channel.close()

    @contextmanager
    def busy(self, record: KernelRecord) -> Iterator[None]:
        if record.state != "ready":
            raise KernelNotReady(record.message or "The kernel is still starting.")
        descriptor = _try_lock(self._folder(record.id) / "busy.lock")
        if descriptor is None:
            raise KernelBusy("The kernel is still running another cell.")
        try:
            (self._folder(record.id) / "cancel").unlink(missing_ok=True)
            self.touch(record)
            yield
        finally:
            os.close(descriptor)

    def cancelled(self, record: KernelRecord) -> bool:
        return (self._folder(record.id) / "cancel").exists()

    async def run_silent(self, record: KernelRecord, code: str) -> ExecutionResult:
        """Run helper code that does not count as a cell (previews, variables)."""
        async with self._client(record) as client:
            return await self._execute(client, code, silent=True, timeout=60)

    async def call(self, record: KernelRecord, code: str) -> Any:
        with self.busy(record):
            return self._marker(await self.run_silent(record, code))

    async def complete(self, record: KernelRecord, code: str, cursor: int) -> dict[str, Any]:
        """Ask the kernel for completions; an empty answer while it runs a cell."""
        try:
            with self.busy(record):
                async with self._client(record) as client:
                    msg_id = client.complete(code, cursor)
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        reply = await client.get_shell_msg(timeout=deadline - time.monotonic())
                        if reply.get("parent_header", {}).get("msg_id") == msg_id:
                            content = reply.get("content", {})
                            return {
                                "matches": list(content.get("matches", []))[:200],
                                "cursor_start": int(content.get("cursor_start", cursor)),
                                "cursor_end": int(content.get("cursor_end", cursor)),
                            }
        except (KernelBusy, KernelNotReady, TimeoutError, asyncio.TimeoutError):
            pass
        return {"matches": [], "cursor_start": cursor, "cursor_end": cursor}

    async def start_run(
        self,
        record: KernelRecord,
        kind: str,
        work: Callable[[Callable[[ExecutionResult], None]], Awaitable[ExecutionResult]],
        *,
        on_finish: Callable[[RunRecord], Awaitable[None]] | None = None,
    ) -> RunRecord:
        """Run ``work`` in the background holding the kernel's busy lock; progress lands on disk."""
        if record.state != "ready":
            raise KernelNotReady(record.message or "The kernel is still starting.")
        # Helper calls (variables, completions) hold the lock for moments, and
        # Run all asks for the next cell as soon as the last one ends: wait out
        # such a call rather than refusing the run. A running cell still refuses.
        path = self._folder(record.id) / "busy.lock"
        deadline = time.monotonic() + RUN_LOCK_WAIT_SECONDS
        while (descriptor := _try_lock(path)) is None:
            if time.monotonic() >= deadline:
                raise KernelBusy("The kernel is still running another cell.")
            await asyncio.sleep(0.05)
        (self._folder(record.id) / "cancel").unlink(missing_ok=True)
        self.touch(record)
        run = RunRecord(id=uuid4().hex, kernel=record.id, kind=kind)
        self._save_run(run)

        def progress(partial: ExecutionResult) -> None:
            run.result = _result_dict(partial)
            self._save_run(run)

        async def body() -> None:
            try:
                try:
                    result = await work(progress)
                except asyncio.CancelledError:
                    result = ExecutionResult("cancelled", None, 0, [])
                except Exception as exc:  # reason: a failed run is reported, never lost
                    result = ExecutionResult(
                        "error",
                        None,
                        0,
                        [KernelOutput(type="error", ename=type(exc).__name__, text=str(exc))],
                    )
                run.status = result.status
                run.finished_at = time.time()
                run.result = _result_dict(result)
                self._finish(record, result)
                self._save_run(run)
            finally:
                # Released only after the final state is on disk, so no reader sees a lost run.
                os.close(descriptor)
            if on_finish is not None:
                with suppress(Exception):
                    await on_finish(run)

        self.spawn(body())
        return run

    def _finish(self, record: KernelRecord, result: ExecutionResult) -> None:
        fresh = self._read(record.id)
        if fresh is None:
            return
        if result.execution_count is not None:
            fresh.execution_count = result.execution_count
        fresh.last_used = time.time()
        self._save(fresh)

    def _save_run(self, run: RunRecord) -> None:
        folder = self._folder(run.kernel) / "runs"
        if folder.exists():
            _write_json(folder / f"{run.id}.json", asdict(run))

    def get_run(self, record: KernelRecord, run_id: str) -> RunRecord:
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise KernelNotFound(run_id)
        try:
            raw = json.loads((self._folder(record.id) / "runs" / f"{run_id}.json").read_text())
        except (OSError, ValueError) as exc:
            raise KernelNotFound(run_id) from exc
        run = RunRecord(**raw)
        if run.status == "running" and self.status(record) != "busy":
            # The worker running it went away: report it rather than spin forever.
            run.status = "error"
            run.result = _result_dict(
                ExecutionResult(
                    "error",
                    None,
                    0,
                    [
                        KernelOutput(
                            type="error",
                            ename="RunLost",
                            text="The run stopped before it finished.",
                        )
                    ],
                )
            )
        self._forget_old_runs(record)
        return run

    def _forget_old_runs(self, record: KernelRecord) -> None:
        cutoff = time.time() - RUN_KEEP_SECONDS
        for file in (self._folder(record.id) / "runs").glob("*.json"):
            with suppress(OSError):
                if file.stat().st_mtime < cutoff:
                    file.unlink()

    async def execute(
        self,
        record: KernelRecord,
        code: str,
        *,
        preview: str | None = None,
        timeout: float,
        progress: Callable[[ExecutionResult], None] | None = None,
    ) -> ExecutionResult:
        """Run a cell. The caller holds the busy lock (see ``start_run``)."""
        async with self._client(record) as client:
            result = await self._execute(
                client, code, timeout=timeout, progress=progress, record=record
            )
            if preview and result.status == "ok":
                result.table = await self._preview(client, preview)
        return result

    async def bind_rows(
        self, record: KernelRecord, name: str, rows: dict[str, Any]
    ) -> ExecutionResult:
        """Bind query rows into the kernel as a data frame called ``name``."""
        if not _NAME.fullmatch(name):
            raise ValueError("Result names are Python identifiers.")
        payload = json.dumps({"columns": rows["columns"], "rows": rows["rows"]}, default=str)
        async with self._client(record) as client:
            run = await self._execute(
                client, f"{name} = __phlo_frame({json.dumps(payload)})", silent=True, timeout=120
            )
            if run.status == "ok":
                run.table = await self._preview(client, name)
        return run

    async def duckdb(
        self,
        record: KernelRecord,
        name: str,
        sql: str,
        *,
        timeout: float,
        progress: Callable[[ExecutionResult], None] | None = None,
    ) -> ExecutionResult:
        """Run SQL over data frames already in the kernel, in-process with DuckDB."""
        if not _NAME.fullmatch(name):
            raise ValueError("Result names are Python identifiers.")
        return await self.execute(
            record,
            f"{name} = __phlo_sql({json.dumps(sql)})",
            preview=name,
            timeout=timeout,
            progress=progress,
        )

    async def variables(self, record: KernelRecord) -> list[dict[str, Any]]:
        return await self.call(record, "__phlo_variables()") or []

    async def packages(self, record: KernelRecord) -> list[dict[str, Any]]:
        return await self.call(record, f"__phlo_packages({json.dumps(record.site)})") or []

    async def _preview(self, client: Any, name: str) -> dict[str, Any] | None:
        run = await self._execute(
            client, f"__phlo_preview({json.dumps(name)}, {PREVIEW_ROWS})", silent=True, timeout=60
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

    async def _execute(
        self,
        client: Any,
        code: str,
        *,
        silent: bool = False,
        timeout: float,
        progress: Callable[[ExecutionResult], None] | None = None,
        record: KernelRecord | None = None,
    ) -> ExecutionResult:
        started = time.monotonic()
        msg_id = client.execute(code, store_history=not silent, allow_stdin=False)
        result = ExecutionResult("ok", None, 0, [])
        size = 0
        last_report = 0.0
        deadline = started + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if record is not None:
                    await self.interrupt(record)
                result.status = "timeout"
                break
            try:
                message = await client.get_iopub_msg(timeout=min(remaining, 1.0))
            except Exception:  # reason: an empty poll is not an error
                continue
            if message.get("parent_header", {}).get("msg_id") != msg_id:
                continue
            kind, content = message["msg_type"], message["content"]
            if kind == "status" and content.get("execution_state") == "idle":
                break
            if kind == "execute_input":
                result.execution_count = None if silent else content.get("execution_count")
                continue
            output = _to_output(kind, content)
            if output is None:
                continue
            if output.type == "error":
                result.status = "error"
                if output.ename == "KeyboardInterrupt" and record is not None:
                    result.status = "cancelled"
            length = len(output.text or "") + len(output.png or "")
            if size + length > MAX_OUTPUT_CHARS:
                result.truncated = True
                continue
            size += length
            result.outputs.append(output)
            if progress is not None and time.monotonic() - last_report > 0.25:
                last_report = time.monotonic()
                result.duration_ms = int((last_report - started) * 1000)
                progress(_copy_running(result))
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result


class HostChannel:
    """One conversation with a kernel through the kernel host's WebSocket.

    Messages are Jupyter messages without signatures: the host signs them with
    the kernel's key, which never leaves the host. Shaped like the parts of
    ``jupyter_client``'s client the registry uses.
    """

    def __init__(self, url: str, kernel_id: str, headers: dict[str, str]) -> None:
        scheme, rest = url.split("://", 1)
        self._url = (
            "wss" if scheme == "https" else "ws"
        ) + f"://{rest}/kernels/{kernel_id}/channels"
        self._headers = headers
        self._session = uuid4().hex
        self._socket: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._sends: set[asyncio.Task[None]] = set()
        self._queues: dict[str, asyncio.Queue[dict[str, Any]]] = {
            "iopub": asyncio.Queue(),
            "shell": asyncio.Queue(),
        }

    async def open(self) -> None:
        from websockets.asyncio.client import connect
        from websockets.exceptions import WebSocketException

        try:
            self._socket = await connect(
                self._url,
                additional_headers=self._headers,
                max_size=64 * 1024 * 1024,
                open_timeout=GATEWAY_SECONDS,
                proxy=None,
            )
        except (OSError, WebSocketException, TimeoutError) as exc:
            raise KernelNotReady("The kernel host did not open a channel to the kernel.") from exc
        self._reader = asyncio.ensure_future(self._read())

    async def _read(self) -> None:
        from websockets.exceptions import ConnectionClosed

        try:
            async for raw in self._socket:
                with suppress(ValueError, KeyError, TypeError):
                    data = json.loads(raw)
                    queue = self._queues.get(data["channel"])
                    if queue is not None and isinstance(data["msg"], dict):
                        queue.put_nowait(data["msg"])
        except ConnectionClosed:
            return

    def _send(self, msg_type: str, content: dict[str, Any]) -> str:
        msg_id = uuid4().hex
        message = {
            "header": {
                "msg_id": msg_id,
                "msg_type": msg_type,
                "session": self._session,
                "username": "phlo",
                "version": "5.3",
            },
            "parent_header": {},
            "metadata": {},
            "content": content,
        }
        task = asyncio.ensure_future(
            self._socket.send(json.dumps({"channel": "shell", "msg": message}))
        )
        self._sends.add(task)
        task.add_done_callback(self._sends.discard)
        return msg_id

    def execute(self, code: str, *, store_history: bool = True, allow_stdin: bool = False) -> str:
        return self._send(
            "execute_request",
            {
                "code": code,
                "silent": False,
                "store_history": store_history,
                "user_expressions": {},
                "allow_stdin": allow_stdin,
                "stop_on_error": True,
            },
        )

    def complete(self, code: str, cursor_pos: int) -> str:
        return self._send("complete_request", {"code": code, "cursor_pos": cursor_pos})

    async def _get(self, channel: str, timeout: float) -> dict[str, Any]:
        if self._reader is not None and self._reader.done() and self._queues[channel].empty():
            raise KernelNotReady("The kernel host closed the channel.")
        return await asyncio.wait_for(self._queues[channel].get(), timeout=max(timeout, 0.0))

    async def get_iopub_msg(self, timeout: float) -> dict[str, Any]:
        return await self._get("iopub", timeout)

    async def get_shell_msg(self, timeout: float) -> dict[str, Any]:
        return await self._get("shell", timeout)

    async def wait_for_ready(self, timeout: float) -> None:
        msg_id = self._send("kernel_info_request", {})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                reply = await self.get_shell_msg(timeout=deadline - time.monotonic())
            except TimeoutError:
                break
            if reply.get("parent_header", {}).get("msg_id") == msg_id:
                return
        raise KernelNotReady("The kernel did not answer.")

    async def close(self) -> None:
        if self._sends:
            await asyncio.gather(*self._sends, return_exceptions=True)
        if self._socket is not None:
            with suppress(Exception):
                await self._socket.close()
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)


def _copy_running(result: ExecutionResult) -> ExecutionResult:
    return ExecutionResult(
        "running",
        result.execution_count,
        result.duration_ms,
        list(result.outputs),
        result.truncated,
    )


def _result_dict(result: ExecutionResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "execution_count": result.execution_count,
        "duration_ms": result.duration_ms,
        "outputs": [asdict(output) for output in result.outputs],
        "truncated": result.truncated,
        "table": result.table,
    }


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
