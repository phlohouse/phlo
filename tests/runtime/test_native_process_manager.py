"""Tests for NativeProcessManager.

Units cover dev-command gating, environment expansion with defaults, path
placeholder resolution (project root, source path, alias), process tracking
and stopping, and PATH setup prepending the project venv bin directory.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from contextlib import suppress
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import phlo.plugins.compose.native as compose_native
from phlo.plugins.compose.native import NativeProcess, NativeProcessManager
from phlo.plugins.discovery import ServiceDefinition


def _svc(name: str, dev: dict | None = None, **kwargs) -> ServiceDefinition:
    """Build a ServiceDefinition fixture, passing extra fields straight through."""
    return ServiceDefinition(
        name=name,
        description=f"{name} service",
        category="core",
        dev=dev if dev is not None else {},
        **kwargs,
    )


def _process_is_live(pid: int) -> bool:
    """Return false for exited processes, including Linux zombies."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False

    stat_path = Path(f"/proc/{pid}/stat")
    if not stat_path.exists():
        return True
    try:
        state = stat_path.read_text().rsplit(") ", 1)[1].split(maxsplit=1)[0]
    except FileNotFoundError:
        return False
    return state != "Z"


class TestCanRunDev:
    """Tests for `NativeProcessManager.can_run_dev`."""

    def test_returns_true_when_dev_has_command(self) -> None:
        """Verify services with a dev command are runnable."""
        mgr = NativeProcessManager(Path("/tmp"))
        svc = _svc("api", dev={"command": ["npm", "start"]})
        assert mgr.can_run_dev(svc) is True

    def test_returns_false_when_no_dev(self) -> None:
        """Verify services without dev config are not runnable."""
        mgr = NativeProcessManager(Path("/tmp"))
        svc = _svc("postgres")
        assert mgr.can_run_dev(svc) is False

    def test_returns_false_when_dev_has_no_command(self) -> None:
        """Verify missing dev command marks service as non-runnable."""
        mgr = NativeProcessManager(Path("/tmp"))
        svc = _svc("api", dev={"environment": {"PORT": "3000"}})
        assert mgr.can_run_dev(svc) is False


class TestExpandEnvVars:
    """Tests for `NativeProcessManager._expand_env_vars`."""

    def test_expands_simple_var(self) -> None:
        """Verify simple placeholder expansion."""
        mgr = NativeProcessManager(Path("/tmp"))
        result = mgr._expand_env_vars(
            "http://${HOST}:${PORT}", {"HOST": "localhost", "PORT": "3000"}
        )
        assert result == "http://localhost:3000"

    def test_uses_default_when_var_missing(self) -> None:
        """Verify default values are used for missing variables."""
        mgr = NativeProcessManager(Path("/tmp"))
        result = mgr._expand_env_vars("${HOST:-0.0.0.0}", {})
        assert result == "0.0.0.0"

    def test_prefers_env_value_over_default(self) -> None:
        """Verify explicit values override defaults."""
        mgr = NativeProcessManager(Path("/tmp"))
        result = mgr._expand_env_vars("${HOST:-0.0.0.0}", {"HOST": "myhost"})
        assert result == "myhost"

    def test_raises_key_error_for_missing_var_without_default(self) -> None:
        """Verify missing required placeholders raise `KeyError`."""
        mgr = NativeProcessManager(Path("/tmp"))
        with pytest.raises(KeyError, match="MISSING"):
            mgr._expand_env_vars("${MISSING}/path", {})

    def test_no_substitution_when_no_vars(self) -> None:
        """Verify plain strings are returned unchanged."""
        mgr = NativeProcessManager(Path("/tmp"))
        assert mgr._expand_env_vars("plain-string", {}) == "plain-string"


class TestResolvePath:
    """Tests for `NativeProcessManager._resolve_path`."""

    def test_replaces_project_root(self) -> None:
        """Verify `{project_root}` placeholders are resolved."""
        mgr = NativeProcessManager(Path("/my/project"))
        svc = _svc("api")
        result = mgr._resolve_path("{project_root}/dist", svc)
        assert result == Path("/my/project/dist")

    def test_replaces_source_path(self) -> None:
        """Verify `{source_path}` placeholders are resolved."""
        mgr = NativeProcessManager(Path("/my/project"))
        svc = _svc("api", source_path=Path("/pkg/src"))
        result = mgr._resolve_path("{source_path}/build", svc)
        assert result == Path("/pkg/src/build")

    def test_replaces_source_alias(self) -> None:
        """Verify `{source}` placeholders are resolved."""
        mgr = NativeProcessManager(Path("/my/project"))
        svc = _svc("api", source_path=Path("/pkg/src"))
        result = mgr._resolve_path("{source}/build", svc)
        assert result == Path("/pkg/src/build")

    def test_returns_plain_path_when_no_placeholders(self) -> None:
        """Verify plain path inputs are converted directly."""
        mgr = NativeProcessManager(Path("/my/project"))
        svc = _svc("api")
        result = mgr._resolve_path("./dist", svc)
        assert result == Path("./dist")


class TestProcessTracking:
    """Tests for process tracking helpers."""

    def test_get_running_services_empty(self) -> None:
        """Verify no processes yields an empty running-service list."""
        mgr = NativeProcessManager(Path("/tmp"))
        assert mgr.get_running_services() == []

    def test_get_process_returns_none_for_unknown(self) -> None:
        """Verify unknown process names return `None`."""
        mgr = NativeProcessManager(Path("/tmp"))
        assert mgr.get_process("nonexistent") is None

    def test_stop_unknown_service_returns_false(self) -> None:
        """Verify stopping unknown services returns `False`."""
        mgr = NativeProcessManager(Path("/tmp"))
        result = asyncio.run(mgr.stop_service("nonexistent"))
        assert result is False

    def test_get_running_services_filters_by_poll(self) -> None:
        """Verify running list excludes exited processes by polling state."""
        mgr = NativeProcessManager(Path("/tmp"))
        running = MagicMock()
        running.poll.return_value = None  # still running
        stopped = MagicMock()
        stopped.poll.return_value = 0  # exited

        mgr._processes["alive"] = NativeProcess(name="alive", process=running)
        mgr._processes["dead"] = NativeProcess(name="dead", process=stopped)

        assert mgr.get_running_services() == ["alive"]

    def test_get_process_returns_process(self) -> None:
        """Verify stored process objects are returned by name."""
        mgr = NativeProcessManager(Path("/tmp"))
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        native = NativeProcess(name="api", process=mock_proc)
        mgr._processes["api"] = native

        assert mgr.get_process("api") is native
        assert mgr.get_process("other") is None


class TestNativeEnvSetup:
    """Tests for native subprocess environment setup."""

    def test_start_service_prepends_project_venv_bin_to_path(self, monkeypatch, tmp_path) -> None:
        mgr = NativeProcessManager(tmp_path)
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        service = _svc("api", dev={"command": ["python", "-m", "http.server"]})

        captured_env: dict[str, str] = {}

        class _Proc:
            pid = 123

            def poll(self):
                return None

        def fake_popen(command, **kwargs):
            captured_env.update(kwargs["env"])
            return _Proc()

        monkeypatch.setattr(compose_native.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(
            NativeProcessManager,
            "_wait_for_health",
            lambda self, url, timeout=30: asyncio.sleep(0, result=True),
        )

        asyncio.run(mgr.start_service(service))

        assert captured_env["PATH"].split(os.pathsep)[0] == str(venv_bin)
        assert captured_env["VIRTUAL_ENV"] == str(tmp_path / ".venv")


def test_start_service_discards_process_when_declared_health_check_fails(
    monkeypatch, tmp_path
) -> None:
    """A failed declared health check is a failed native start."""
    mgr = NativeProcessManager(tmp_path)
    service = _svc(
        "api",
        dev={"command": ["python", "-m", "http.server"], "health_check": "http://bad"},
    )

    class _Proc:
        pid = 123

        def poll(self):
            return None

        def send_signal(self, _signal):
            pass

        def wait(self, timeout):
            del timeout

    monkeypatch.setattr(compose_native.subprocess, "Popen", lambda *_args, **_kwargs: _Proc())
    monkeypatch.setattr(
        NativeProcessManager,
        "_wait_for_health",
        lambda self, url, timeout=30: asyncio.sleep(0, result=False),
    )

    result = asyncio.run(mgr.start_service(service))

    assert result is None
    assert mgr.get_process("api") is None


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="process groups require POSIX")
def test_failed_health_cleanup_terminates_native_process_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Failed health cleanup reaps the native leader and its child process."""
    pid_file = tmp_path / "child.pid"
    wrapper = (
        "import pathlib, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); "
        "time.sleep(60)"
    )
    service = _svc(
        "api",
        dev={
            "command": [sys.executable, "-c", wrapper, str(pid_file)],
            "health_check": "http://unhealthy",
        },
    )
    manager = NativeProcessManager(tmp_path)

    async def fail_after_child_starts(_self, _url: str, timeout: int = 30) -> bool:
        """Wait until the wrapper has published its child before failing health."""
        deadline = time.monotonic() + timeout
        while not pid_file.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert pid_file.exists()
        return False

    monkeypatch.setattr(NativeProcessManager, "_wait_for_health", fail_after_child_starts)

    try:
        result = asyncio.run(manager.start_service(service))

        assert result is None
        assert manager.get_process("api") is None
        assert pid_file.exists()
        child_pid = int(pid_file.read_text())

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not _process_is_live(child_pid):
                break
            time.sleep(0.05)
        else:
            pytest.fail("failed health cleanup left the native child process running")
    finally:
        if pid_file.exists():
            with suppress(ProcessLookupError):
                os.kill(int(pid_file.read_text()), signal.SIGKILL)


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="process groups require POSIX")
def test_stop_service_escalates_after_leader_exits_but_child_ignores_sigterm(
    tmp_path: Path,
) -> None:
    """Shutdown escalates when a native descendant outlives its leader."""
    pid_file = tmp_path / "ignoring-child.pid"
    wrapper = (
        "import subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import os, pathlib, signal, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)', sys.argv[1]]); "
        "time.sleep(60)"
    )
    service = _svc(
        "api",
        dev={"command": [sys.executable, "-c", wrapper, str(pid_file)]},
    )
    manager = NativeProcessManager(tmp_path, log_dir=tmp_path / "logs")
    native_process = asyncio.run(manager.start_service(service))
    assert native_process is not None
    leader_pid = native_process.pid

    try:
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_file.exists()
        child_pid = int(pid_file.read_text())

        assert asyncio.run(manager.stop_service("api", timeout=0.1)) is True
        assert manager.get_process("api") is None
        assert native_process.log_file is None

        while time.monotonic() < deadline:
            if not _process_is_live(child_pid):
                break
            time.sleep(0.05)
        else:
            pytest.fail("native child ignoring SIGTERM survived group cleanup")
    finally:
        with suppress(ProcessLookupError):
            os.killpg(leader_pid, signal.SIGKILL)
