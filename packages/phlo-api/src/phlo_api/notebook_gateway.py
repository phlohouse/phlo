"""Notebook kernel host: runs Observatory notebook kernels away from the API.

This module is the whole program inside the ``phlo-notebook-kernels`` container.
It imports nothing from phlo, so the image carries only Jupyter and the data
libraries notebooks use.

Isolation, all enforced by the operating system rather than by the API:

- Every kernel runs as its own Unix user, drawn from a pool, in a private
  ``0700`` folder. Its connection file (which holds the message signing key)
  is readable only by that user, so one kernel can neither read another's
  files nor sign messages to it.
- Kernel users may not open network connections at all. ``iptables`` rejects
  every new outbound connection from the kernel uid range, loopback included;
  the host only answers connections the host itself opened. So kernels cannot
  reach Trino, MinIO, the API, the internet or each other's sockets. Lakehouse
  data reaches a kernel only as rows the API sends through this host.
- Kernels get no API credentials: the container holds none besides the host's
  own bearer token, which only root can read.
- Package installs run as a separate installer user that may reach public
  package indexes over HTTP(S) but no private address, and install wheels only,
  so installing never runs package code.

The host refuses to start unless it runs as root, the token is configured and a
probe proves a kernel user cannot connect anywhere. There is no weaker mode.

The API talks to the host over HTTP with a bearer token: REST calls to start,
stop, interrupt and restart kernels, and one WebSocket per conversation that
carries Jupyter messages. The host signs and verifies messages with each
kernel's key, so the key never leaves the container.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

logger = logging.getLogger("phlo.notebook_gateway")

PORT = 8890
MAX_KERNELS = 8
KERNEL_UID_FIRST = 20001
KERNEL_UID_LAST = 20064
INSTALLER_UID = 19999
IDLE_SECONDS = 35 * 60
READY_SECONDS = 60.0
INSTALL_SECONDS = 900
MAX_PACKAGES = 50
MAX_MESSAGE_BYTES = 64 * 1024 * 1024
TOKEN_MIN_LENGTH = 32
_ID = re.compile(r"^[a-f0-9]{16,32}$")
_REQUIREMENT = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9._,-]+\])?([<>=!~]=?[^\s;@]+(,[<>=!~]=?[^\s;@]+)*)?$"
)
# The only requests a client may send to a kernel. Interrupts go through REST.
_SHELL_REQUESTS = {"execute_request", "complete_request", "kernel_info_request", "inspect_request"}
_KERNEL_ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TZ")
_KERNEL_LAUNCH = (
    "import os, resource, sys\n"
    "os.umask(0o077)\n"
    "resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))\n"
    "resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))\n"
    "os.execv(sys.executable, [sys.executable, '-m', 'ipykernel_launcher', '-f', sys.argv[1]])\n"
)


class HostError(RuntimeError):
    """A request the host cannot honour; ``status`` is the HTTP status to answer."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


# -- launching ---------------------------------------------------------------------


class Launcher:
    """Starts kernel processes and owns their folders. Subclasses decide the user."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def prepare(self) -> None:
        raise NotImplementedError

    def allocate(self, kernels: Sequence[Kernel]) -> int:
        raise NotImplementedError

    def kernel_folder(self, kernel: Kernel) -> Path:
        return self.root / "kernels" / kernel.id

    def envs_folder(self) -> Path:
        return self.root / "envs"

    def make_folder(self, kernel: Kernel) -> Path:
        raise NotImplementedError

    def spawn(self, kernel: Kernel, python: str, connection: Path) -> subprocess.Popen[bytes]:
        raise NotImplementedError

    def run_installer(self, command: list[str], env: dict[str, str], log: Path) -> int:
        raise NotImplementedError

    def reap(self, kernel: Kernel) -> None:
        """Stop every process the kernel's user still runs, then remove its folder."""
        raise NotImplementedError


class IsolatedLauncher(Launcher):
    """Production launcher: one Unix user per kernel, no network for kernel users."""

    def prepare(self) -> None:
        if os.geteuid() != 0:
            raise SystemExit(
                "The notebook kernel host must start as root to run kernels as separate users."
            )
        (self.root / "kernels").mkdir(parents=True, exist_ok=True)
        os.chmod(self.root / "kernels", 0o711)
        envs = self.envs_folder()
        envs.mkdir(parents=True, exist_ok=True)
        os.chown(envs, INSTALLER_UID, INSTALLER_UID)
        os.chmod(envs, 0o755)
        home = self.root / "installer"
        home.mkdir(parents=True, exist_ok=True)
        os.chown(home, INSTALLER_UID, INSTALLER_UID)
        os.chmod(home, 0o700)
        # Leftovers from a previous run are never adopted.
        for folder in (self.root / "kernels").iterdir():
            shutil.rmtree(folder, ignore_errors=True)
        for uid in range(KERNEL_UID_FIRST, KERNEL_UID_LAST + 1):
            _kill_user(uid)
        _verify_no_network(KERNEL_UID_FIRST)

    def allocate(self, kernels: Sequence[Kernel]) -> int:
        used = {kernel.uid for kernel in kernels}
        for uid in range(KERNEL_UID_FIRST, KERNEL_UID_LAST + 1):
            if uid not in used and not _user_processes(uid):
                return uid
        raise HostError(409, "kernel_limit", "No kernel user is free. Stop a kernel first.")

    def make_folder(self, kernel: Kernel) -> Path:
        folder = self.kernel_folder(kernel)
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(mode=0o700)
        (folder / "work").mkdir(mode=0o700)
        for path in (folder, folder / "work"):
            os.chown(path, kernel.uid, kernel.uid)
        return folder

    def spawn(self, kernel: Kernel, python: str, connection: Path) -> subprocess.Popen[bytes]:
        os.chown(connection, kernel.uid, kernel.uid)
        os.chmod(connection, 0o600)
        folder = self.kernel_folder(kernel)
        log = open(self.root / "kernels" / f"{kernel.id}.log", "ab")  # noqa: SIM115  # reason: the child process owns this file
        try:
            return subprocess.Popen(
                [python, "-I", "-c", _KERNEL_LAUNCH, str(connection)],
                env=_kernel_environment(folder),
                cwd=folder / "work",
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                user=kernel.uid,
                group=kernel.uid,
                extra_groups=[],
                process_group=0,
            )
        finally:
            log.close()

    def run_installer(self, command: list[str], env: dict[str, str], log: Path) -> int:
        with open(log, "ab") as stream:
            done = subprocess.run(
                command,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=INSTALL_SECONDS,
                user=INSTALLER_UID,
                group=INSTALLER_UID,
                extra_groups=[],
                umask=0o022,
                check=False,
            )
        return done.returncode

    def reap(self, kernel: Kernel) -> None:
        if kernel.process is not None:
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(kernel.process.pid, signal.SIGKILL)
            with suppress(subprocess.TimeoutExpired):
                kernel.process.wait(timeout=2)
        _kill_user(kernel.uid)
        shutil.rmtree(self.kernel_folder(kernel), ignore_errors=True)
        with suppress(OSError):
            (self.root / "kernels" / f"{kernel.id}.log").unlink()


class SameUserLauncher(Launcher):
    """Runs kernels as the current user. For the API's tests only: no isolation.

    Never selected by :func:`main`; tests construct it directly.
    """

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self._next = KERNEL_UID_FIRST

    def prepare(self) -> None:
        (self.root / "kernels").mkdir(parents=True, exist_ok=True)
        self.envs_folder().mkdir(parents=True, exist_ok=True)

    def allocate(self, kernels: Sequence[Kernel]) -> int:
        self._next += 1
        return self._next

    def make_folder(self, kernel: Kernel) -> Path:
        folder = self.kernel_folder(kernel)
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        (folder / "work").mkdir(exist_ok=True)
        return folder

    def spawn(self, kernel: Kernel, python: str, connection: Path) -> subprocess.Popen[bytes]:
        folder = self.kernel_folder(kernel)
        log = open(folder / "kernel.log", "ab")  # noqa: SIM115  # reason: the child process owns this file
        try:
            return subprocess.Popen(
                [python, "-m", "ipykernel_launcher", "-f", str(connection)],
                env=_kernel_environment(folder),
                cwd=folder / "work",
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                process_group=0,
            )
        finally:
            log.close()

    def run_installer(self, command: list[str], env: dict[str, str], log: Path) -> int:
        with open(log, "ab") as stream:
            return subprocess.run(
                command,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=INSTALL_SECONDS,
                check=False,
            ).returncode

    def reap(self, kernel: Kernel) -> None:
        if kernel.process is not None:
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(kernel.process.pid, signal.SIGKILL)
            with suppress(subprocess.TimeoutExpired):
                kernel.process.wait(timeout=2)
        shutil.rmtree(self.kernel_folder(kernel), ignore_errors=True)


def _kernel_environment(folder: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key in _KERNEL_ENV_KEEP}
    env.update(
        {
            "HOME": str(folder / "work"),
            "TMPDIR": str(folder / "work"),
            "PYTHONUNBUFFERED": "1",
            "MPLCONFIGDIR": str(folder / "work"),
            # Jupyter's inline backend, so plt.show() and a figure as the last
            # line come back as images; a GUI backend would have nowhere to draw.
            "MPLBACKEND": "module://matplotlib_inline.backend_inline",
        }
    )
    return env


def _user_processes(uid: int) -> list[int]:
    found = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            with suppress(OSError):
                if entry.stat().st_uid == uid:
                    found.append(int(entry.name))
    return found


def _kill_user(uid: int) -> None:
    for _ in range(20):
        pids = _user_processes(uid)
        if not pids:
            return
        for pid in pids:
            with suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)
        time.sleep(0.05)


def _verify_no_network(uid: int) -> None:
    """Prove a kernel user cannot open a connection, or refuse to start."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        probe = (
            "import socket, sys\n"
            "try:\n"
            f"    socket.create_connection(('127.0.0.1', {port}), timeout=3)\n"
            "except OSError:\n"
            "    sys.exit(0)\n"
            "sys.exit(1)\n"
        )
        done = subprocess.run(
            [sys.executable, "-I", "-c", probe],
            user=uid,
            group=uid,
            extra_groups=[],
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            timeout=10,
            check=False,
        )
    if done.returncode != 0:
        raise SystemExit(
            "Kernel users can open network connections. The host needs its iptables rules "
            "(NET_ADMIN) before it runs kernels; refusing to start."
        )


# -- kernels ------------------------------------------------------------------------


@dataclass
class Kernel:
    id: str
    uid: int
    packages: list[str]
    state: str = "starting"  # installing | starting | ready | failed
    message: str | None = None
    site: str | None = None
    connection: dict[str, Any] | None = None
    process: subprocess.Popen[bytes] | None = None
    last_used: float = field(default_factory=time.time)

    def view(self) -> dict[str, Any]:
        alive = self.process is not None and self.process.poll() is None
        state = self.state
        if state == "ready" and not alive:
            state = "failed"
        return {
            "id": self.id,
            "state": state,
            "message": self.message if state != "failed" or self.message else "The kernel stopped.",
            "packages": self.packages,
            "site": self.site,
        }


def normalize_packages(packages: Any) -> list[str]:
    """Plain ``name[extras]<specifier>`` requirements only: no URLs, paths, markers or options."""
    if not isinstance(packages, list) or len(packages) > MAX_PACKAGES:
        raise HostError(422, "packages_invalid", f"List at most {MAX_PACKAGES} packages.")
    normalized = []
    for raw in packages:
        text = raw.strip() if isinstance(raw, str) else ""
        if not text or len(text) > 200 or not _REQUIREMENT.fullmatch(text.replace(" ", "")):
            raise HostError(422, "packages_invalid", f"'{raw}' is not a package requirement.")
        normalized.append(text.replace(" ", ""))
    return sorted(set(normalized), key=str.lower)


class KernelHost:
    """Kernels and package environments, in memory; nothing survives a restart."""

    def __init__(self, launcher: Launcher, *, package_index: str | None = None) -> None:
        self.launcher = launcher
        self.package_index = package_index
        self.kernels: dict[str, Kernel] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._env_locks: dict[str, asyncio.Lock] = {}
        self._lock = asyncio.Lock()

    def get(self, kernel_id: str) -> Kernel:
        kernel = self.kernels.get(kernel_id) if _ID.fullmatch(kernel_id or "") else None
        if kernel is None:
            raise HostError(404, "kernel_not_found", "No such kernel.")
        kernel.last_used = time.time()
        return kernel

    async def start(self, kernel_id: str, packages: Any) -> Kernel:
        if not _ID.fullmatch(kernel_id or ""):
            raise HostError(422, "kernel_invalid", "Kernel ids are 16 to 32 hex characters.")
        wanted = normalize_packages(packages)
        async with self._lock:
            if kernel_id in self.kernels:
                raise HostError(409, "kernel_exists", "That kernel already exists.")
            if len(self.kernels) >= MAX_KERNELS:
                raise HostError(409, "kernel_limit", "All notebook kernels are in use.")
            uid = self.launcher.allocate(list(self.kernels.values()))
            kernel = Kernel(id=kernel_id, uid=uid, packages=wanted)
            kernel.state = "installing" if wanted and not self._env_ready(wanted) else "starting"
            self.kernels[kernel_id] = kernel
        self._spawn_task(self._bring_up(kernel))
        return kernel

    def _spawn_task(self, coroutine: Any) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _bring_up(self, kernel: Kernel) -> None:
        try:
            if kernel.packages:
                await self._ensure_environment(kernel.packages)
                kernel.state = "starting"
            await asyncio.to_thread(self._launch, kernel)
            await self._wait_ready(kernel)
        except Exception as exc:  # reason: any start failure is reported on the kernel
            logger.warning("kernel_start_failed", extra={"kernel": kernel.id})
            kernel.state = "failed"
            kernel.message = str(exc)[-2000:] or "The kernel did not start."
            await asyncio.to_thread(self.launcher.reap, kernel)
            return
        kernel.state = "ready"
        kernel.message = None

    def _launch(self, kernel: Kernel) -> None:
        from jupyter_client.connect import write_connection_file

        folder = self.launcher.make_folder(kernel)
        connection = folder / "kernel.json"
        filename, info = write_connection_file(str(connection), transport="tcp", ip="127.0.0.1")
        kernel.connection = dict(info)
        if isinstance(kernel.connection.get("key"), bytes):
            kernel.connection["key"] = kernel.connection["key"].decode()
        python, kernel.site = self._python(kernel.packages)
        kernel.process = self.launcher.spawn(kernel, python, Path(filename))

    async def _wait_ready(self, kernel: Kernel) -> None:
        client = self.client(kernel)
        client.start_channels()
        try:
            await client.wait_for_ready(timeout=READY_SECONDS)
        finally:
            client.stop_channels()

    def client(self, kernel: Kernel) -> Any:
        from jupyter_client.asynchronous import AsyncKernelClient

        if kernel.connection is None:
            raise HostError(409, "kernel_starting", "The kernel is still starting.")
        client = AsyncKernelClient()
        client.load_connection_info(cast(Any, kernel.connection))
        return client

    async def stop(self, kernel: Kernel) -> None:
        self.kernels.pop(kernel.id, None)
        await asyncio.to_thread(self.launcher.reap, kernel)

    def interrupt(self, kernel: Kernel) -> None:
        if kernel.process is not None and kernel.process.poll() is None:
            with suppress(ProcessLookupError, PermissionError):
                os.kill(kernel.process.pid, signal.SIGINT)

    async def restart(self, kernel: Kernel) -> Kernel:
        await asyncio.to_thread(self.launcher.reap, kernel)
        kernel.process = None
        kernel.connection = None
        kernel.state = "starting"
        await self._bring_up(kernel)
        return kernel

    async def reap_idle(self) -> None:
        cutoff = time.time() - IDLE_SECONDS
        for kernel in list(self.kernels.values()):
            if kernel.last_used < cutoff:
                await self.stop(kernel)

    async def stop_all(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for kernel in list(self.kernels.values()):
            await self.stop(kernel)

    # -- package environments -------------------------------------------------------

    def _env_key(self, packages: list[str]) -> str:
        payload = json.dumps([sys.version, packages, self.package_index])
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def _env_dir(self, packages: list[str]) -> Path:
        return self.launcher.envs_folder() / self._env_key(packages)

    def _env_ready(self, packages: list[str]) -> bool:
        return (self._env_dir(packages) / ".ready").exists()

    def _python(self, packages: list[str]) -> tuple[str, str | None]:
        if not packages:
            return sys.executable, None
        import sysconfig

        folder = self._env_dir(packages)
        site = sysconfig.get_path("purelib", vars={"base": str(folder), "platbase": str(folder)})
        return str(folder / "bin" / "python"), site

    async def _ensure_environment(self, packages: list[str]) -> None:
        key = self._env_key(packages)
        lock = self._env_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not self._env_ready(packages):
                await asyncio.to_thread(self._build_environment, packages)

    def _build_environment(self, packages: list[str]) -> None:
        import sysconfig

        folder = self._env_dir(packages)
        log = self.launcher.envs_folder() / f"{folder.name}.log"
        shutil.rmtree(folder, ignore_errors=True)
        uv = shutil.which("uv")
        if uv is None:
            raise RuntimeError("Installing packages needs uv in the kernel host image.")
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(self.launcher.root / "installer"),
            "UV_CACHE_DIR": str(self.launcher.root / "installer" / "cache"),
            "UV_NO_PROGRESS": "1",
        }
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE"):
            if key in os.environ:
                env[key] = os.environ[key]
        python = folder / "bin" / "python"
        # Wheels only: installing never runs a package's own build code.
        install = [uv, "pip", "install", "--python", str(python), "--only-binary", ":all:"]
        if self.package_index:
            install += ["--index-url", self.package_index]
        if self.launcher.run_installer(
            [uv, "venv", "--python", sys.executable, str(folder)], env, log
        ):
            raise RuntimeError(_tail(log, "Creating the package environment failed."))
        site = Path(
            sysconfig.get_path("purelib", vars={"base": str(folder), "platbase": str(folder)})
        )
        base = sorted({sysconfig.get_path("purelib"), sysconfig.get_path("platlib")})
        # The notebook's own versions win: the base path comes after its site-packages.
        pth = site / "_phlo_base.pth"
        pth.write_text("\n".join(base) + "\n")
        with suppress(PermissionError):
            os.chown(pth, INSTALLER_UID, INSTALLER_UID)
        if self.launcher.run_installer([*install, *packages], env, log):
            raise RuntimeError(_tail(log, "Installing packages failed."))
        ready = folder / ".ready"
        ready.touch()
        with suppress(PermissionError):
            os.chown(ready, INSTALLER_UID, INSTALLER_UID)


def _tail(log: Path, headline: str) -> str:
    with suppress(OSError):
        lines = log.read_text(errors="replace").strip().splitlines()[-12:]
        return headline + "\n" + "\n".join(lines)
    return headline


# -- HTTP ---------------------------------------------------------------------------


def make_app(host: KernelHost, token: str) -> Any:
    """The host's HTTP application. Every route but /health needs the bearer token."""
    import tornado.web
    import tornado.websocket

    expected = token.encode()

    def authorized(handler: tornado.web.RequestHandler) -> bool:
        header = handler.request.headers.get("Authorization", "")
        scheme, _, value = header.partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(value.encode(), expected)

    class Base(tornado.web.RequestHandler):
        def prepare(self) -> None:
            if not authorized(self):
                self.set_status(401)
                self.finish({"error": "unauthorized"})

        def write_error(self, status_code: int, **kwargs: Any) -> None:
            error = kwargs.get("exc_info", (None, None))[1]
            if isinstance(error, HostError):
                self.set_status(error.status)
                self.finish({"error": error.code, "message": str(error)})
                return
            self.finish({"error": "internal", "message": "The kernel host failed."})

        def body(self) -> dict[str, Any]:
            try:
                value = json.loads(self.request.body or b"{}")
            except ValueError as exc:
                raise HostError(400, "bad_request", "The body is not JSON.") from exc
            if not isinstance(value, dict):
                raise HostError(400, "bad_request", "The body is not an object.")
            return value

        def send_error_for(self, error: HostError) -> None:
            self.set_status(error.status)
            self.finish({"error": error.code, "message": str(error)})

    class Health(tornado.web.RequestHandler):
        def get(self) -> None:
            self.finish({"status": "ok"})

    class Kernels(Base):
        def get(self) -> None:
            self.finish({"kernels": [kernel.view() for kernel in host.kernels.values()]})

        async def post(self) -> None:
            try:
                payload = self.body()
                kernel = await host.start(str(payload.get("id", "")), payload.get("packages", []))
            except HostError as error:
                self.send_error_for(error)
                return
            self.set_status(201)
            self.finish(kernel.view())

    class OneKernel(Base):
        def get(self, kernel_id: str) -> None:
            try:
                self.finish(host.get(kernel_id).view())
            except HostError as error:
                self.send_error_for(error)

        async def delete(self, kernel_id: str) -> None:
            with suppress(HostError):
                await host.stop(host.get(kernel_id))
            self.set_status(204)
            self.finish()

    class Interrupt(Base):
        def post(self, kernel_id: str) -> None:
            try:
                kernel = host.get(kernel_id)
            except HostError as error:
                self.send_error_for(error)
                return
            host.interrupt(kernel)
            self.finish(kernel.view())

    class Restart(Base):
        async def post(self, kernel_id: str) -> None:
            try:
                kernel = await host.restart(host.get(kernel_id))
            except HostError as error:
                self.send_error_for(error)
                return
            self.finish(kernel.view())

    class Channels(tornado.websocket.WebSocketHandler):
        """Jupyter messages both ways; the host signs them with the kernel's key."""

        client: Any = None
        kernel: Kernel | None = None
        pumps: list[asyncio.Task[Any]]

        def check_origin(self, origin: str) -> bool:
            return False  # Never a browser: only the API, which sends no Origin.

        async def get(self, *args: Any, **kwargs: Any) -> None:
            if not authorized(self):
                self.set_status(401)
                self.finish({"error": "unauthorized"})
                return
            await super().get(*args, **kwargs)

        def open(self, kernel_id: str) -> None:  # type: ignore[override]  # reason: tornado passes path groups
            try:
                kernel = host.get(kernel_id)
                if kernel.state != "ready":
                    raise HostError(409, "kernel_starting", "The kernel is not ready.")
                self.kernel = kernel
                self.client = host.client(kernel)
            except HostError as error:
                self.close(code=4000 + error.status, reason=error.code)
                return
            self.client.start_channels()
            self.pumps = [
                asyncio.ensure_future(self._pump("iopub", self.client.get_iopub_msg)),
                asyncio.ensure_future(self._pump("shell", self.client.get_shell_msg)),
            ]

        async def _pump(self, channel: str, receive: Callable[..., Any]) -> None:
            while True:
                try:
                    message = await receive(timeout=1.0)
                except Exception:  # reason: an empty poll is not an error
                    if self.ws_connection is None:
                        return
                    continue
                if self.kernel is not None:
                    self.kernel.last_used = time.time()
                message.pop("buffers", None)
                try:
                    await self.write_message(
                        json.dumps({"channel": channel, "msg": message}, default=str)
                    )
                except tornado.websocket.WebSocketClosedError:
                    return

        def on_message(self, message: str | bytes) -> None:
            try:
                data = json.loads(message)
                message = data["msg"]
                if (
                    data.get("channel") != "shell"
                    or message["header"]["msg_type"] not in _SHELL_REQUESTS
                ):
                    raise ValueError("refused")
                if not isinstance(message.get("content"), dict):
                    raise ValueError("refused")
            except (ValueError, KeyError, TypeError):
                self.close(code=4400, reason="message refused")
                return
            if self.kernel is not None:
                self.kernel.last_used = time.time()
            # Re-signed with this kernel's key; the client's own header is kept so
            # its replies are matched to its msg_id.
            outgoing = self.client.session.msg(
                message["header"]["msg_type"],
                content=message["content"],
                header={
                    **self.client.session.msg_header(message["header"]["msg_type"]),
                    "msg_id": str(message["header"].get("msg_id") or uuid4().hex),
                },
                metadata=message.get("metadata")
                if isinstance(message.get("metadata"), dict)
                else {},
            )
            self.client.shell_channel.send(outgoing)

        def on_close(self) -> None:
            for pump in getattr(self, "pumps", []):
                pump.cancel()
            if self.client is not None:
                with suppress(Exception):
                    self.client.stop_channels()

    kernel_id = r"([a-f0-9]{16,32})"
    return tornado.web.Application(
        [
            (r"/health", Health),
            (r"/kernels", Kernels),
            (rf"/kernels/{kernel_id}", OneKernel),
            (rf"/kernels/{kernel_id}/interrupt", Interrupt),
            (rf"/kernels/{kernel_id}/restart", Restart),
            (rf"/kernels/{kernel_id}/channels", Channels),
        ],
        websocket_max_message_size=MAX_MESSAGE_BYTES,
        max_body_size=1024 * 1024,
    )


def read_token() -> str:
    path = os.environ.get("PHLO_NOTEBOOK_GATEWAY_TOKEN_FILE")
    token = (
        Path(path).read_text().strip()
        if path
        else os.environ.get("PHLO_NOTEBOOK_GATEWAY_TOKEN", "")
    )
    os.environ.pop("PHLO_NOTEBOOK_GATEWAY_TOKEN", None)
    if len(token) < TOKEN_MIN_LENGTH:
        raise SystemExit(
            f"PHLO_NOTEBOOK_GATEWAY_TOKEN must be at least {TOKEN_MIN_LENGTH} characters. "
            "Run phlo services init to generate it."
        )
    return token


def package_index() -> str | None:
    index = os.environ.get("PHLO_NOTEBOOK_PACKAGE_INDEX") or None
    if index is not None and not index.startswith(("https://", "http://")):
        raise SystemExit("PHLO_NOTEBOOK_PACKAGE_INDEX must be an http(s) URL.")
    return index


async def serve(host: KernelHost, token: str, port: int) -> None:
    app = make_app(host, token)
    server = app.listen(port, address="0.0.0.0")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    try:
        while not stop.is_set():
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=60)
            await host.reap_idle()
    finally:
        server.stop()
        await host.stop_all()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    token = read_token()
    launcher = IsolatedLauncher(
        Path(os.environ.get("PHLO_NOTEBOOK_HOST_ROOT", "/var/lib/phlo-kernels"))
    )
    launcher.prepare()
    host = KernelHost(launcher, package_index=package_index())
    logger.info("notebook kernel host ready on port %s", PORT)
    asyncio.run(serve(host, token, PORT))


if __name__ == "__main__":
    main()
