"""Jupyter kernels for Observatory notebooks, shared by every API worker on one host.

Each kernel belongs to one signed-in person and one notebook. Kernels start
with a scrubbed environment, so API secrets (Trino passwords, tokens) never
reach notebook code. Lakehouse data enters a kernel only as rows from a
validated read-only query, bound under a name the person chose.

Kernels are plain ``ipykernel`` processes described by a record in a private
folder (``PHLO_NOTEBOOK_KERNEL_DIR``), so their state survives an API reload and
a stray kernel is never lost track of. The API runs as one process. Cells run
as background runs whose output is written to the kernel's folder as it
arrives, so a long cell never holds an HTTP request open.

Kernel output is plain text, PNG images and structured table previews. HTML
output is dropped on purpose: Observatory renders tables with its own
components, never markup produced by notebook code.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import sysconfig
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
_ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT")
# Installers may need the host's proxy and certificate settings; kernels never get them.
_INSTALL_ENV_PREFIXES = ("PIP_", "UV_", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_")
_INSTALL_ENV_KEYS = {"REQUESTS_CA_BUNDLE", "http_proxy", "https_proxy", "no_proxy", "HOME"}

# Runs once in every new or restarted kernel. Helpers are underscored so the
# Variables panel hides them. The watchdog stops a kernel whose API has gone
# away: the API touches ``alive`` on every use.
_PREAMBLE = r"""
import json as __phlo_json

def __phlo_watchdog(path, idle):
    import os, threading, time
    def check():
        while True:
            time.sleep(30)
            try:
                if time.time() - os.path.getmtime(path) > idle:
                    os._exit(0)
            except OSError:
                os._exit(0)
    threading.Thread(target=check, daemon=True, name="phlo-watchdog").start()

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
    """Jupyter is not installed, or the host cannot keep kernel state safely."""


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


def kernel_environment() -> dict[str, str]:
    """Only locale and PATH reach a kernel; credentials in the API process stay there."""
    env = {key: value for key, value in os.environ.items() if key in _ENV_KEEP}
    env["PYTHONUNBUFFERED"] = "1"
    # Jupyter's inline backend, so plt.show() and a figure as the last line
    # come back as images; a GUI backend would have nowhere to draw.
    env["MPLBACKEND"] = "module://matplotlib_inline.backend_inline"
    return env


def _install_environment() -> dict[str, str]:
    env = kernel_environment()
    for key, value in os.environ.items():
        if key in _INSTALL_ENV_KEYS or key.startswith(_INSTALL_ENV_PREFIXES):
            env[key] = value
    return env


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
        normalized[requirement.name.lower()] = str(requirement)
    return [normalized[key] for key in sorted(normalized)]


def _lock_nb(descriptor: int) -> None:
    module = cast(Any, _fcntl)
    module.flock(descriptor, module.LOCK_EX | module.LOCK_NB)


def _clean(text: str) -> str:
    return _ANSI.sub("", text)


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        with suppress(OSError, IndexError):
            if stat.read_text().rsplit(")", 1)[1].split()[0] == "Z":
                return False
    return True


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
    """Kernel records in a private folder owned by the API user."""

    def __init__(self, root: Path | None = None, package_index: str | None = None) -> None:
        self._root = root
        self._package_index = package_index
        self._children: dict[int, subprocess.Popen[bytes]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._run_hook: Callable[[KernelRecord, RunRecord, str | None], Awaitable[None]] | None = (
            None
        )

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

    def configure(self, *, root: Path | None, package_index: str | None) -> None:
        """Apply operator settings; the folder is created on first use."""
        if root is not None:
            self._root = root
        self._package_index = package_index

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
        folder = self._folder(record.id)
        alive = folder / "alive"
        alive.touch()
        record.last_used = time.time()

    # -- lookup ------------------------------------------------------------------

    def get(self, kernel_id: str, actor: str) -> KernelRecord:
        record = self._read(kernel_id)
        if record is None or record.actor != actor:
            raise KernelNotFound(kernel_id)
        if record.state == "ready" and not _alive(record.pid):
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

    def prune(self) -> None:
        """Drop dead kernels, stop idle ones and forget stalled starts."""
        cutoff = time.time() - IDLE_SECONDS
        for pid, child in list(self._children.items()):
            if child.poll() is not None:
                self._children.pop(pid, None)
        for record in self.records():
            if record.state == "ready" and not _alive(record.pid):
                self._remove(record)
            elif record.state in {"installing", "starting"} and record.started_at < cutoff:
                self._remove(record)
            elif record.last_used < cutoff and self.status(record) != "busy":
                self._stop_process(record)
                self._remove(record)

    async def start(
        self, *, actor: str, env: str, path: str, packages: list[str], allow_packages: bool
    ) -> KernelRecord:
        """Start (or reuse) this person's kernel for a notebook.

        Kernels without packages are ready when this returns. A kernel whose
        notebook lists packages comes back ``installing`` and becomes ready in
        the background once its environment is built.
        """
        _jupyter()
        packages = normalize_packages(packages)
        if packages and not allow_packages:
            raise PackagesDisabled(
                "This notebook lists Python packages, and an operator has not allowed them. "
                "Set PHLO_NOTEBOOK_PACKAGES=1, or remove the packages."
            )
        async with _aflock(self.root / "registry.lock"):
            await asyncio.to_thread(self.prune)
            for existing in self.for_actor(actor):
                if existing.path == path and existing.env == env:
                    if existing.packages == packages and existing.state != "failed":
                        self.touch(existing)
                        self._save(existing)
                        return existing
                    await asyncio.to_thread(self._stop_process, existing)
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
            (folder / "work").mkdir()
            (folder / "runs").mkdir()
            (folder / "alive").touch()
            record.state = (
                "installing" if packages and not self._env_ready(packages) else "starting"
            )
            self._save(record)
        if record.state == "installing":
            self.spawn(self._install_then_launch(record))
            return record
        await self._launch(record)
        return record

    def spawn(self, coroutine: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _install_then_launch(self, record: KernelRecord) -> None:
        try:
            await self.ensure_environment(record.packages)
        except Exception as exc:  # reason: any installer failure is reported on the kernel
            record.state = "failed"
            record.message = str(exc)[-2000:] or "Installing packages failed."
            self._save(record)
            return
        record.state = "starting"
        self._save(record)
        with suppress(Exception):
            await self._launch(record)

    async def _launch(self, record: KernelRecord) -> None:
        folder = self._folder(record.id)
        python, site = self._python(record.packages)
        try:
            pid = await asyncio.to_thread(self._spawn_process, folder, python)
            record.pid, record.python, record.site = pid, python, site
            self._save(record)
            async with self._client(record) as client:
                await self._execute(
                    client,
                    _PREAMBLE
                    + f"\n__phlo_watchdog({json.dumps(str(folder / 'alive'))}, {IDLE_SECONDS + 300})\n",
                    silent=True,
                    timeout=READY_SECONDS,
                )
        except Exception as exc:
            self._stop_process(record)
            record.state = "failed"
            record.message = f"The kernel did not start: {exc}"[:2000]
            self._save(record)
            raise
        record.state = "ready"
        record.message = None
        record.execution_count = 0
        self.touch(record)
        self._save(record)

    def _spawn_process(self, folder: Path, python: str) -> int:
        from jupyter_client.connect import write_connection_file

        connection = folder / "kernel.json"
        connection.unlink(missing_ok=True)
        # IPC sockets in the kernel's own private folder: nothing listens on TCP.
        write_connection_file(str(connection), transport="ipc", ip=str(folder / "k"))
        with open(folder / "kernel.log", "ab") as log:
            child = subprocess.Popen(
                [python, "-m", "ipykernel_launcher", "-f", str(connection)],
                env=kernel_environment(),
                cwd=folder / "work",
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        self._children[child.pid] = child
        return child.pid

    def _stop_process(self, record: KernelRecord) -> None:
        if not record.pid or not _alive(record.pid):
            return
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(record.pid, signal.SIGTERM)
        deadline = time.monotonic() + 3
        while _alive(record.pid) and time.monotonic() < deadline:
            child = self._children.get(record.pid)
            if child is not None:
                child.poll()
            time.sleep(0.05)
        if _alive(record.pid):
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(record.pid, signal.SIGKILL)
        child = self._children.pop(record.pid, None)
        if child is not None:
            with suppress(subprocess.TimeoutExpired):
                child.wait(timeout=2)

    def _remove(self, record: KernelRecord) -> None:
        shutil.rmtree(self.root / record.id, ignore_errors=True)

    async def shutdown(self, record: KernelRecord) -> None:
        await asyncio.to_thread(self._stop_process, record)
        self._remove(record)

    async def shutdown_all(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._root is None or not self._root.exists():
            return
        for record in self.records():
            if record.pid in self._children:
                await self.shutdown(record)

    def interrupt(self, record: KernelRecord) -> None:
        (self._folder(record.id) / "cancel").touch()
        if record.pid and _alive(record.pid):
            with suppress(ProcessLookupError, PermissionError):
                os.kill(record.pid, signal.SIGINT)

    async def restart(self, record: KernelRecord) -> KernelRecord:
        descriptor = _try_lock(self._folder(record.id) / "busy.lock")
        if descriptor is None:
            self.interrupt(record)
            await asyncio.sleep(0.5)
        else:
            os.close(descriptor)
        await asyncio.to_thread(self._stop_process, record)
        record.state = "starting"
        self._save(record)
        await self._launch(record)
        return record

    # -- package environments ----------------------------------------------------

    def _env_key(self, packages: list[str]) -> str:
        payload = json.dumps([sys.version, packages, self._package_index])
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def _env_dir(self, packages: list[str]) -> Path:
        return self.root / "envs" / self._env_key(packages)

    def _env_ready(self, packages: list[str]) -> bool:
        return (self._env_dir(packages) / ".ready").exists()

    def _python(self, packages: list[str]) -> tuple[str, str | None]:
        if not packages:
            return sys.executable, None
        folder = self._env_dir(packages)
        python = folder / "bin" / "python"
        site = sysconfig.get_path("purelib", vars={"base": str(folder), "platbase": str(folder)})
        return str(python), site

    async def ensure_environment(self, packages: list[str]) -> Path:
        """Build (once per package set) a virtual environment layered over the API's packages."""
        folder = self._env_dir(packages)
        if (folder / ".ready").exists():
            return folder
        (self.root / "envs").mkdir(mode=0o700, exist_ok=True)
        lock = self.root / "envs" / f"{folder.name}.lock"
        await asyncio.to_thread(self._build_environment, folder, packages, lock)
        return folder

    def _build_environment(self, folder: Path, packages: list[str], lock: Path) -> None:
        with _flock(lock, wait=900):
            if (folder / ".ready").exists():
                return
            shutil.rmtree(folder, ignore_errors=True)
            index = self._package_index
            uv = shutil.which("uv")
            log = folder.parent / f"{folder.name}.log"
            env = _install_environment()
            python = folder / "bin" / "python"
            if uv:
                steps = [[uv, "venv", "--python", sys.executable, str(folder)]]
                install = [uv, "pip", "install", "--python", str(python)]
            else:
                steps = [[sys.executable, "-m", "venv", str(folder)]]
                install = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
            if index:
                install += ["--index-url", index]
            steps.append([*install, *packages])
            with open(log, "wb") as stream:
                for index_step, command in enumerate(steps):
                    if index_step == 1:
                        self._layer_base_packages(folder)
                    done = subprocess.run(
                        command, stdout=stream, stderr=subprocess.STDOUT, env=env, timeout=900
                    )
                    if done.returncode != 0:
                        tail = log.read_text(errors="replace").strip().splitlines()[-12:]
                        raise RuntimeError("Installing packages failed:\n" + "\n".join(tail))
            (folder / ".ready").touch()

    @staticmethod
    def _layer_base_packages(folder: Path) -> None:
        """Let the notebook environment import the API's own packages (ipykernel, pandas, duckdb).

        The ``.pth`` path is appended after the environment's own site-packages, so a
        version the notebook asked for wins over the base one.
        """
        site = Path(
            sysconfig.get_path("purelib", vars={"base": str(folder), "platbase": str(folder)})
        )
        site.mkdir(parents=True, exist_ok=True)
        base = {sysconfig.get_path("purelib"), sysconfig.get_path("platlib")}
        (site / "_phlo_base.pth").write_text("\n".join(sorted(base)) + "\n")

    # -- running code ------------------------------------------------------------

    @asynccontextmanager
    async def _client(self, record: KernelRecord) -> AsyncIterator[Any]:
        from jupyter_client.asynchronous import AsyncKernelClient

        client = AsyncKernelClient()
        client.load_connection_file(str(self._folder(record.id) / "kernel.json"))
        client.start_channels()
        try:
            await client.wait_for_ready(timeout=READY_SECONDS)
            yield client
        finally:
            client.stop_channels()

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
                    self.interrupt(record)
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


def _jupyter() -> None:
    if (
        importlib.util.find_spec("ipykernel") is None
        or importlib.util.find_spec("jupyter_client") is None
    ):
        raise KernelsUnavailable(
            "Notebook kernels need the phlo-api 'notebooks' extra (jupyter-client and ipykernel)."
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
