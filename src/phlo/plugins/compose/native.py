"""
Native Process Manager

Manages native (subprocess) execution of services without Docker.
Used for running phlo-api and Observatory natively.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from re import Match
from typing import TextIO

import httpx

from phlo.logging import get_logger
from phlo.plugins.discovery import ServiceDefinition

logger = get_logger(__name__)


@dataclass
class NativeProcess:
    """Represents a running native process."""

    name: str
    process: subprocess.Popen[str]
    health_check_url: str | None = None
    started_at: float = field(default_factory=time.time)
    log_file: TextIO | None = None

    @property
    def is_running(self) -> bool:
        """Check if process is still running."""
        return self.process.poll() is None

    @property
    def pid(self) -> int:
        """Get process ID."""
        return self.process.pid

    def close_log_file(self) -> None:
        """Close the process log file handle when one is open."""

        if self.log_file is None:
            return
        try:
            self.log_file.close()
        except Exception:
            logger.exception("Failed to close log file for native process")
        finally:
            self.log_file = None


class NativeProcessManager:
    """Manages native processes for services in dev mode (no Docker)."""

    def __init__(self, project_root: Path, log_dir: Path | None = None):
        """Initialize a native process manager.

        ``project_root`` resolves service paths; ``log_dir`` optionally receives
        per-service log files.
        """

        self.project_root = project_root
        self.log_dir = log_dir
        self._processes: dict[str, NativeProcess] = {}

    def can_run_dev(self, service: ServiceDefinition) -> bool:
        """Check if a service can run in dev mode as a subprocess."""
        return bool(service.dev and service.dev.get("command"))

    def _expand_env_vars(self, value: str, env: dict[str, str]) -> str:
        """Expand ``${VAR}`` and ``${VAR:-default}`` placeholders in ``value``.

        Substitutes from ``env``; raise KeyError when a placeholder has neither a
        matching env value nor a default.
        """

        pattern = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")

        def repl(match: Match[str]) -> str:
            """Resolve a single environment placeholder match."""

            var = match.group(1)
            default = match.group(2)
            if var in env:
                return env[var]
            if default is not None:
                return default
            raise KeyError(var)

        return pattern.sub(repl, value)

    async def start_service(
        self,
        service: ServiceDefinition,
        env_overrides: dict[str, str] | None = None,
    ) -> NativeProcess | None:
        """Start a service as a subprocess in dev mode.

        Return the NativeProcess handle, or None when the service is not supported.
        """
        if not self.can_run_dev(service):
            logger.warning("service_dev_mode_not_supported", service_name=service.name)
            return None

        dev_config = service.dev
        command = dev_config.get("command", [])

        if not command:
            logger.error("service_missing_dev_command", service_name=service.name)
            return None

        # Build environment
        env = os.environ.copy()
        if dev_env := dev_config.get("environment"):
            env.update(
                {
                    k: self._expand_env_vars(v, env) if isinstance(v, str) else str(v)
                    for k, v in dev_env.items()
                }
            )
        if env_overrides:
            env.update({k: self._expand_env_vars(v, env) for k, v in env_overrides.items()})

        project_venv = self.project_root / ".venv"
        project_venv_bin = project_venv / "bin"
        if project_venv_bin.exists():
            env["PATH"] = f"{project_venv_bin}{os.pathsep}{env.get('PATH', '')}"
            env["VIRTUAL_ENV"] = str(project_venv)

        command = [self._expand_env_vars(arg, env) for arg in command if isinstance(arg, str)]

        # Resolve working directory
        cwd_template = dev_config.get("cwd", ".")
        cwd = self._resolve_path(cwd_template, service)

        # Handle build step if required
        if dev_config.get("requires_build"):
            should_build = True
            build_if_missing = dev_config.get("build_if_missing")
            if isinstance(build_if_missing, str):
                build_target = (cwd / build_if_missing).resolve()
                if build_target.exists():
                    should_build = False
            build_cmd = dev_config.get("build_command", [])
            if build_cmd and should_build:
                logger.info("service_build_started", service_name=service.name)
                try:
                    build_result = subprocess.run(
                        build_cmd,
                        cwd=cwd,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=300,  # 5 minute timeout for builds
                    )
                    if build_result.returncode != 0:
                        logger.error(
                            "service_build_failed",
                            service_name=service.name,
                            stderr=build_result.stderr,
                        )
                        return None
                except subprocess.TimeoutExpired:
                    logger.error("service_build_timed_out", service_name=service.name)
                    return None

        # Start the process
        logger.info(
            "service_dev_starting",
            service_name=service.name,
            command=" ".join(command),
        )
        log_file: TextIO | None = None
        try:
            stdout = None
            if self.log_dir is not None:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                log_path = self.log_dir / f"{service.name}.log"
                log_file = log_path.open("a", encoding="utf-8")  # noqa: SIM115
                stdout = log_file
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdout=stdout,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        except Exception:
            logger.exception("service_start_failed", service_name=service.name)
            if log_file is not None:
                try:
                    log_file.close()
                except Exception:
                    logger.exception("Failed to close log file after start failure")
            return None

        health_check_url = dev_config.get("health_check")
        if isinstance(health_check_url, str):
            health_check_url = self._expand_env_vars(health_check_url, env)
        native_process = NativeProcess(
            name=service.name,
            process=process,
            health_check_url=health_check_url,
            log_file=log_file,
        )
        self._processes[service.name] = native_process

        if not native_process.is_running:
            logger.warning("service_exited_during_start", service_name=service.name)
            native_process.close_log_file()
            del self._processes[service.name]
            return None

        # Wait for health check if configured
        if health_check_url:
            healthy = await self._wait_for_health(health_check_url, timeout=30)
            if not healthy:
                logger.warning(
                    "service_health_check_failed_after_start",
                    service_name=service.name,
                )
                await self.stop_service(service.name)
                return None

        if not native_process.is_running:
            logger.warning("service_exited_during_start", service_name=service.name)
            native_process.close_log_file()
            del self._processes[service.name]
            return None

        return native_process

    async def stop_service(self, name: str, timeout: float = 10) -> bool:
        """Stop a native service, waiting up to ``timeout`` seconds for shutdown.

        Return True when stopped; False when not found or shutdown failed.
        """
        native_process = self._processes.get(name)
        if not native_process:
            return False

        process = native_process.process
        if not native_process.is_running:
            native_process.close_log_file()
            del self._processes[name]
            return True

        # Each subprocess starts a session, so target its whole process group.
        # A service command can delegate to children which would otherwise keep
        # its port bound after the leader exits.
        logger.info("service_stopping", service_name=name, pid=process.pid)
        try:
            self._signal_process_group(process, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception:
            logger.exception("service_stop_failed", service_name=name)
            return False

        deadline = time.monotonic() + timeout
        if not await self._wait_for_process_group_exit(process, deadline):
            logger.warning("service_force_kill", service_name=name)
            try:
                self._signal_process_group(process, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception:
                logger.exception("service_force_kill_failed", service_name=name)
                return False
            # A successful group SIGKILL terminates every live member. Do not
            # wait for killpg(..., 0) to fail: Linux still reports zombies as
            # process-group members until their eventual parent reaps them.

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.error("service_leader_survived_shutdown", service_name=name)
            return False
        except Exception:
            logger.exception("service_reap_failed", service_name=name)
            return False

        native_process.close_log_file()
        del self._processes[name]
        return True

    async def stop_all(self, timeout: int = 10) -> None:
        """Stop all running native services."""
        for name in list(self._processes.keys()):
            await self.stop_service(name, timeout)

    def get_running_services(self) -> list[str]:
        """Get list of running service names."""
        return [name for name, proc in self._processes.items() if proc.is_running]

    def get_process(self, name: str) -> NativeProcess | None:
        """Get a native process by name."""
        return self._processes.get(name)

    def _signal_process_group(self, process: subprocess.Popen[str], sig: signal.Signals) -> None:
        """Signal a native process group, falling back to its leader."""
        try:
            os.killpg(process.pid, sig)
        except (AttributeError, OSError):
            process.send_signal(sig)

    async def _wait_for_process_group_exit(
        self, process: subprocess.Popen[str], deadline: float
    ) -> bool:
        """Wait until a native process group is gone before ``deadline``."""
        while self._process_group_exists(process):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(remaining, 0.1))
        return True

    def _process_group_exists(self, process: subprocess.Popen[str]) -> bool:
        """Return whether a native process group still has a member."""
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return False
        except (AttributeError, OSError):
            return process.poll() is None
        return True

    def _resolve_path(self, template: str, service: ServiceDefinition) -> Path:
        """Resolve path template."""
        resolved = template
        if "{project_root}" in resolved:
            resolved = resolved.replace("{project_root}", str(self.project_root))
        if "{source}" in resolved and service.source_path:
            resolved = resolved.replace("{source}", str(service.source_path))
        if "{source_path}" in resolved and service.source_path:
            resolved = resolved.replace("{source_path}", str(service.source_path))
        return Path(resolved)

    async def _wait_for_health(self, url: str, timeout: int = 30) -> bool:
        """Wait for health check to pass."""
        start = time.time()
        async with httpx.AsyncClient(timeout=5.0) as client:
            while time.time() - start < timeout:
                try:
                    response = await client.get(url)
                    if response.status_code < 500:
                        return True
                except Exception:
                    logger.debug("health_check_poll_failed", url=url)
                await asyncio.sleep(1)
        return False
