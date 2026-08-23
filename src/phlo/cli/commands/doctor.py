"""Phlo doctor: diagnose the local environment and running services.

Runs grouped probes (container backend, project config, plugin
discovery, ports, live services) that each yield a DiagnosticResult with
ok/warn/fail/skip status and an optional fix hint; individual probe
failures are captured, never fatal. Output renders as a table or JSON,
and diagnostics run with stdout silenced to keep CLI output clean.
Imported by the phlo CLI main entry point and exposed to phlo-api's authoring endpoints.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass, field
from enum import StrEnum
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
import yaml

if TYPE_CHECKING:
    from phlo.cli.commands.services.ports import PortMapping

ServiceDiscovery: Any | None = None


def _get_service_discovery_class() -> Any:
    global ServiceDiscovery
    if ServiceDiscovery is None:
        from phlo.plugins.discovery import ServiceDiscovery as discovered_service_discovery

        ServiceDiscovery = discovered_service_discovery
    return ServiceDiscovery


class DiagnosticStatus(StrEnum):
    """Severity level of a diagnostic outcome."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class DiagnosticResult:
    """A single diagnostic outcome, optionally with a fix hint and detail payload."""

    id: str
    group: str
    status: DiagnosticStatus
    message: str
    fix: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-serializable dict describing this result."""
        payload: dict[str, Any] = {
            "id": self.id,
            "group": self.group,
            "status": self.status.value,
            "message": self.message,
        }
        if self.fix:
            payload["fix"] = self.fix
        if self.details:
            payload["details"] = self.details
        return payload


def summarize(results: list[DiagnosticResult]) -> dict[str, int]:
    """Count diagnostic results per status."""
    return {
        status.value: sum(1 for result in results if result.status == status)
        for status in DiagnosticStatus
    }


def render_json(results: list[DiagnosticResult]) -> str:
    """Render diagnostics as an indented JSON document with a summary."""
    return json.dumps(
        {
            "summary": summarize(results),
            "checks": [result.to_payload() for result in results],
        },
        indent=2,
        sort_keys=True,
    )


def render_terminal(results: list[DiagnosticResult]) -> str:
    """Render diagnostics as human-readable terminal text."""
    lines = ["Phlo Doctor", ""]
    groups = list(dict.fromkeys(result.group for result in results))
    for group in groups:
        lines.append(group)
        for result in [item for item in results if item.group == group]:
            lines.append(f"  {result.status.value:<5} {result.message}")
            if result.fix:
                lines.append(f"        Fix: {result.fix}")
        lines.append("")
    summary = summarize(results)
    lines.append(
        "Summary: "
        f"{summary['ok']} ok, {summary['warn']} warnings, "
        f"{summary['fail']} failures, {summary['skip']} skipped"
    )
    return "\n".join(lines)


def _run_probe(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=10)


def _probe_failure_details(exc: BaseException, *, verbose: bool) -> dict[str, Any]:
    if not verbose:
        return {}
    return {"error": str(exc), "type": type(exc).__name__}


def _configured_container_backend() -> str:
    configured = os.environ.get("PHLO_CONTAINER_BACKEND")
    if configured and configured.strip():
        return configured.strip().lower()

    project_file = Path.cwd() / "phlo.yaml"
    if not project_file.exists():
        return "docker"

    try:
        loaded = yaml.safe_load(project_file.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return "docker"
    if not isinstance(loaded, dict):
        return "docker"
    infrastructure = loaded.get("infrastructure")
    if not isinstance(infrastructure, dict):
        return "docker"
    configured = infrastructure.get("container_backend")
    if isinstance(configured, str) and configured.strip():
        return configured.strip().lower()
    return "docker"


def _selected_container_backend(configured: str) -> str | None:
    if configured != "auto":
        return configured
    if shutil.which("docker"):
        return "docker"
    if shutil.which("podman"):
        return "podman"
    return None


def _check_docker_backend(*, verbose: bool) -> list[DiagnosticResult]:
    results: list[DiagnosticResult] = []
    docker_path = shutil.which("docker")
    results.append(
        DiagnosticResult(
            "env.docker.cli",
            "Environment",
            DiagnosticStatus.OK if docker_path else DiagnosticStatus.FAIL,
            "Docker CLI found" if docker_path else "Docker CLI not found",
            None if docker_path else "Install Docker Desktop or ensure docker is on PATH.",
        )
    )

    if not docker_path:
        return results

    try:
        compose_cmd = ["docker", "compose", "version"]
        compose = _run_probe(compose_cmd)
        if compose.returncode != 0 and shutil.which("docker-compose"):
            fallback_cmd = ["docker-compose", "version"]
            fallback = _run_probe(fallback_cmd)
            if fallback.returncode == 0:
                compose_cmd = fallback_cmd
                compose = fallback
    except (OSError, subprocess.SubprocessError) as exc:
        results.append(
            DiagnosticResult(
                "env.docker.compose",
                "Environment",
                DiagnosticStatus.FAIL,
                "Docker Compose probe failed",
                "Ensure Docker Desktop is running and docker compose version responds.",
                _probe_failure_details(exc, verbose=verbose),
            )
        )
    else:
        details = {"command": " ".join(compose_cmd)}
        if verbose and compose.stderr:
            details["stderr"] = compose.stderr.strip()
        results.append(
            DiagnosticResult(
                "env.docker.compose",
                "Environment",
                DiagnosticStatus.OK if compose.returncode == 0 else DiagnosticStatus.FAIL,
                "Docker Compose available"
                if compose.returncode == 0
                else "Docker Compose is not available",
                None
                if compose.returncode == 0
                else "Install Docker Compose v2 or update Docker Desktop.",
                details,
            )
        )
    return results


def _check_podman_backend(*, verbose: bool) -> list[DiagnosticResult]:
    results: list[DiagnosticResult] = []
    podman_path = shutil.which("podman")
    results.append(
        DiagnosticResult(
            "env.podman.cli",
            "Environment",
            DiagnosticStatus.OK if podman_path else DiagnosticStatus.FAIL,
            "Podman CLI found" if podman_path else "Podman CLI not found",
            None if podman_path else "Install Podman Desktop or ensure podman is on PATH.",
        )
    )

    if not podman_path:
        return results

    try:
        info = _run_probe(["podman", "info"])
    except (OSError, subprocess.SubprocessError) as exc:
        results.append(
            DiagnosticResult(
                "env.podman.info",
                "Environment",
                DiagnosticStatus.FAIL,
                "Podman info probe failed",
                "Start Podman with `podman machine start`, then retry.",
                _probe_failure_details(exc, verbose=verbose),
            )
        )
    else:
        details = {"stderr": info.stderr.strip()} if verbose and info.stderr else {}
        results.append(
            DiagnosticResult(
                "env.podman.info",
                "Environment",
                DiagnosticStatus.OK if info.returncode == 0 else DiagnosticStatus.FAIL,
                "Podman engine available"
                if info.returncode == 0
                else "Podman engine is not available",
                None
                if info.returncode == 0
                else "Start Podman with `podman machine start`, then retry.",
                details,
            )
        )

    try:
        compose = _run_probe(["podman", "compose", "version"])
    except (OSError, subprocess.SubprocessError) as exc:
        results.append(
            DiagnosticResult(
                "env.podman.compose",
                "Environment",
                DiagnosticStatus.FAIL,
                "Podman compose probe failed",
                "Install or configure a Podman compose provider.",
                _probe_failure_details(exc, verbose=verbose),
            )
        )
    else:
        details = {"stderr": compose.stderr.strip()} if verbose and compose.stderr else {}
        results.append(
            DiagnosticResult(
                "env.podman.compose",
                "Environment",
                DiagnosticStatus.OK if compose.returncode == 0 else DiagnosticStatus.FAIL,
                "Podman compose available"
                if compose.returncode == 0
                else "Podman compose is not available",
                None
                if compose.returncode == 0
                else "Install or configure a Podman compose provider.",
                details,
            )
        )
    return results


def check_environment(*, verbose: bool = False) -> list[DiagnosticResult]:
    """Check the Python version and container backend availability."""
    results: list[DiagnosticResult] = [
        DiagnosticResult(
            "doctor.bootstrap", "Environment", DiagnosticStatus.OK, "Doctor command loaded"
        )
    ]
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    python_status = DiagnosticStatus.OK if sys.version_info >= (3, 11) else DiagnosticStatus.FAIL
    results.append(
        DiagnosticResult(
            "env.python",
            "Environment",
            python_status,
            f"Python {python_version}",
            None if python_status == DiagnosticStatus.OK else "Install Python 3.11 or newer.",
        )
    )

    uv_path = shutil.which("uv")
    results.append(
        DiagnosticResult(
            "env.uv",
            "Environment",
            DiagnosticStatus.OK if uv_path else DiagnosticStatus.FAIL,
            "uv found" if uv_path else "uv command not found",
            None if uv_path else "Install uv from https://docs.astral.sh/uv/.",
        )
    )

    configured_backend = _configured_container_backend()
    selected_backend = _selected_container_backend(configured_backend)
    if configured_backend not in {"docker", "podman", "auto"}:
        results.append(
            DiagnosticResult(
                "env.container_backend",
                "Environment",
                DiagnosticStatus.FAIL,
                f"Unsupported container backend: {configured_backend}",
                "Set infrastructure.container_backend or PHLO_CONTAINER_BACKEND to docker, podman, or auto.",
            )
        )
    elif selected_backend is None:
        results.append(
            DiagnosticResult(
                "env.container_backend",
                "Environment",
                DiagnosticStatus.FAIL,
                "No container backend CLI found",
                "Install Docker Desktop or Podman Desktop, then rerun phlo doctor.",
            )
        )
    else:
        results.append(
            DiagnosticResult(
                "env.container_backend",
                "Environment",
                DiagnosticStatus.OK,
                f"Container backend: {selected_backend}",
                details={"configured": configured_backend}
                if verbose and configured_backend != selected_backend
                else {},
            )
        )
        if selected_backend == "docker":
            results.extend(_check_docker_backend(verbose=verbose))
        elif selected_backend == "podman":
            results.extend(_check_podman_backend(verbose=verbose))

    _, _, free = shutil.disk_usage(Path.cwd())
    free_gb = free // (1024**3)
    results.append(
        DiagnosticResult(
            "env.disk",
            "Environment",
            DiagnosticStatus.OK if free_gb >= 10 else DiagnosticStatus.WARN,
            f"{free_gb} GB free in current filesystem",
            None if free_gb >= 10 else "Free at least 10 GB before starting the full local stack.",
        )
    )
    return results


def check_project(*, verbose: bool = False) -> list[DiagnosticResult]:
    """Check project configuration, compose file, and env files."""
    results: list[DiagnosticResult] = []
    project_file = Path.cwd() / "phlo.yaml"
    if not project_file.exists():
        return [
            DiagnosticResult(
                "project.config",
                "Project",
                DiagnosticStatus.SKIP,
                "phlo.yaml not found",
                "Run this command inside a Phlo project, or create one with phlo init.",
            )
        ]

    try:
        loaded = yaml.safe_load(project_file.read_text()) or {}
        status = DiagnosticStatus.OK if isinstance(loaded, dict) else DiagnosticStatus.FAIL
        results.append(
            DiagnosticResult(
                "project.config",
                "Project",
                status,
                "phlo.yaml parsed"
                if status == DiagnosticStatus.OK
                else "phlo.yaml must contain a mapping",
                None
                if status == DiagnosticStatus.OK
                else "Fix phlo.yaml so the top level is a YAML mapping.",
            )
        )
    except (OSError, yaml.YAMLError) as exc:
        results.append(
            DiagnosticResult(
                "project.config",
                "Project",
                DiagnosticStatus.FAIL,
                "phlo.yaml could not be read or parsed",
                "Fix YAML syntax and file permissions, then rerun phlo doctor.",
                {"error": str(exc)} if verbose else {},
            )
        )

    phlo_dir = Path.cwd() / ".phlo"
    compose_file = phlo_dir / "docker-compose.yml"
    if not compose_file.exists():
        results.append(
            DiagnosticResult(
                "project.compose",
                "Project",
                DiagnosticStatus.WARN,
                ".phlo/docker-compose.yml is missing",
                "Run phlo services init.",
            )
        )
    else:
        try:
            compose_config = yaml.safe_load(compose_file.read_text()) or {}
            compose_ok = isinstance(compose_config, dict)
            results.append(
                DiagnosticResult(
                    "project.compose",
                    "Project",
                    DiagnosticStatus.OK if compose_ok else DiagnosticStatus.FAIL,
                    ".phlo/docker-compose.yml parsed"
                    if compose_ok
                    else ".phlo/docker-compose.yml must contain a mapping",
                    None if compose_ok else "Run phlo services init --force.",
                )
            )
        except (OSError, yaml.YAMLError) as exc:
            results.append(
                DiagnosticResult(
                    "project.compose",
                    "Project",
                    DiagnosticStatus.FAIL,
                    ".phlo/docker-compose.yml could not be read or parsed",
                    "Run phlo services init --force.",
                    {"error": str(exc)} if verbose else {},
                )
            )
    for filename in (".env", ".env.local"):
        path = phlo_dir / filename
        results.append(
            DiagnosticResult(
                f"project.{filename}",
                "Project",
                DiagnosticStatus.OK if path.exists() else DiagnosticStatus.WARN,
                f".phlo/{filename} found" if path.exists() else f".phlo/{filename} is missing",
                None if path.exists() else "Run phlo services init.",
            )
        )
    return results


def _collect_service_plugin_failures() -> list[dict[str, str]]:
    from phlo.plugins.discovery import discover_plugins

    failures: list[dict[str, str]] = []
    discover_plugins(
        plugin_type="service",
        auto_register=False,
        failure_level="debug",
        failure_sink=failures,
    )
    return failures


def check_discovery(*, verbose: bool = False) -> list[DiagnosticResult]:
    """Check plugin entry-point loading and service discovery."""
    results: list[DiagnosticResult] = []
    try:
        failures = _collect_service_plugin_failures()
    except Exception as exc:
        failures = []
        results.append(
            DiagnosticResult(
                "discovery.entry_points",
                "Discovery",
                DiagnosticStatus.WARN,
                "Could not inspect service plugin entry points",
                "Run phlo doctor --verbose to inspect the discovery exception.",
                {"error": str(exc), "type": type(exc).__name__} if verbose else {},
            )
        )
    if failures:
        results.append(
            DiagnosticResult(
                "discovery.entry_points",
                "Discovery",
                DiagnosticStatus.FAIL,
                f"{len(failures)} service plugin entry point(s) failed to load",
                "Run phlo doctor --verbose to inspect failed plugin entry points.",
                {"failures": failures} if verbose else {},
            )
        )

    try:
        services = _get_service_discovery_class()().discover()
    except Exception as exc:
        results.append(
            DiagnosticResult(
                "discovery.services",
                "Discovery",
                DiagnosticStatus.FAIL,
                "Service discovery failed",
                "Run phlo doctor --verbose to inspect the discovery exception.",
                {"error": str(exc), "type": type(exc).__name__} if verbose else {},
            )
        )
        return results
    results.append(
        DiagnosticResult(
            "discovery.services",
            "Discovery",
            DiagnosticStatus.OK,
            f"Discovered {len(services)} services",
        )
    )
    return results


def _project_name() -> str:
    from phlo.cli.infrastructure.utils import get_project_name

    return get_project_name()


def _collect_port_mappings() -> list[PortMapping]:
    from phlo.cli.commands.services import ports as ports_module

    phlo_dir = Path.cwd() / ".phlo"
    if not phlo_dir.exists():
        return []
    config_file = Path.cwd() / "phlo.yaml"
    config = yaml.safe_load(config_file.read_text()) if config_file.exists() else {}
    config = config if isinstance(config, dict) else {}
    env = ports_module._load_environment(phlo_dir, config)
    services = _get_service_discovery_class()().discover()
    running = ports_module._get_running_container_ports(_project_name())
    _, disabled = ports_module.get_enabled_disabled_service_names(config)
    service_overrides = (
        config.get("services", {}) if isinstance(config.get("services"), dict) else {}
    )
    traefik = ports_module._get_active_traefik_context(
        services,
        env,
        running,
        disabled,
        service_overrides,
    )
    service_routes = ports_module._get_service_routes(services, traefik)
    mappings: list[PortMapping] = []
    for service in services.values():
        if service.name in disabled:
            continue
        service_override = service_overrides.get(service.name, {})
        mappings.extend(
            ports_module._get_service_ports(
                service,
                env,
                running,
                True,
                service_override=service_override if isinstance(service_override, dict) else None,
                service_routes=service_routes,
            )
        )
    return mappings


def check_ports(*, verbose: bool = False) -> list[DiagnosticResult]:
    """Check configured service ports for running-service conflicts."""
    try:
        mappings = _collect_port_mappings()
    except Exception as exc:
        return [
            DiagnosticResult(
                "ports.resolve",
                "Ports",
                DiagnosticStatus.WARN,
                "Could not resolve configured service ports",
                "Run phlo services ports for focused port diagnostics.",
                {"error": str(exc)} if verbose else {},
            )
        ]
    by_port: dict[int, list[PortMapping]] = defaultdict(list)
    for mapping in mappings:
        if mapping.status != "Running":
            continue
        by_port[mapping.host_port].append(mapping)
    conflicts = {port: items for port, items in by_port.items() if len(items) > 1}
    if conflicts:
        rendered = "; ".join(
            f"{port}: {', '.join(item.service for item in items)}"
            for port, items in sorted(conflicts.items())
        )
        return [
            DiagnosticResult(
                "ports.conflicts",
                "Ports",
                DiagnosticStatus.FAIL,
                f"Port conflicts detected: {rendered}",
                "Change the conflicting port values in phlo.yaml env or .phlo/.env.local.",
            )
        ]
    return [
        DiagnosticResult(
            "ports.conflicts", "Ports", DiagnosticStatus.OK, "No configured port conflicts"
        )
    ]


def check_live_services(*, verbose: bool = False) -> list[DiagnosticResult]:
    """Check that the generated compose file for live services exists."""
    compose_file = Path.cwd() / ".phlo" / "docker-compose.yml"
    if not compose_file.exists():
        return [
            DiagnosticResult(
                "live.services",
                "Live Services",
                DiagnosticStatus.SKIP,
                "No generated compose file found",
                "Run phlo services init before checking live services.",
            )
        ]
    return [
        DiagnosticResult(
            "live.services",
            "Live Services",
            DiagnosticStatus.OK,
            "Generated compose file is present for live service checks",
        )
    ]


def run_diagnostics(*, verbose: bool = False) -> list[DiagnosticResult]:
    """Run every diagnostic check and return the combined results."""
    return [
        *check_environment(verbose=verbose),
        *check_project(verbose=verbose),
        *check_discovery(verbose=verbose),
        *check_ports(verbose=verbose),
        *check_live_services(verbose=verbose),
    ]


@contextmanager
def _silence_stdout() -> Iterator[None]:
    """Silence every stdout channel while diagnostics run.

    Probes and plugin imports can emit output through three layers: the fd 1
    descriptor (subprocesses and C extensions), logging handler streams, and
    `sys.stdout`. Each is redirected separately and restored on exit.
    """
    saved_stdout_fd: int | None = None
    saved_handler_streams: list[tuple[Callable[[Any], Any], Any]] = []
    with Path(os.devnull).open("w") as devnull, StringIO() as stdout_buffer:
        try:
            saved_stdout_fd = os.dup(1)
            os.dup2(devnull.fileno(), 1)
        except OSError:
            if saved_stdout_fd is not None:
                os.close(saved_stdout_fd)
            saved_stdout_fd = None

        for handler in logging.getLogger().handlers:
            stream = getattr(handler, "stream", None)
            set_stream = getattr(handler, "setStream", None)
            if stream is None or not callable(set_stream):
                continue
            saved_handler_streams.append((set_stream, stream))
            set_stream(devnull)

        try:
            with redirect_stdout(stdout_buffer):
                yield
        finally:
            for set_stream, stream in saved_handler_streams:
                set_stream(stream)
            if saved_stdout_fd is not None:
                os.dup2(saved_stdout_fd, 1)
                os.close(saved_stdout_fd)


def _run_diagnostics_quietly(*, verbose: bool = False) -> list[DiagnosticResult]:
    previous_disable_level = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with _silence_stdout():
            return run_diagnostics(verbose=verbose)
    finally:
        logging.disable(previous_disable_level)


@click.command("doctor")
@click.option("--json", "output_json", is_flag=True, help="Output diagnostics as JSON.")
@click.option("--verbose", is_flag=True, help="Include exception details where available.")
def doctor_cmd(output_json: bool, verbose: bool) -> None:
    """Diagnose local Phlo setup and service health.

    Exits with status 1 when any diagnostic reports a failure.
    """
    results = _run_diagnostics_quietly(verbose=verbose)
    click.echo(render_json(results) if output_json else render_terminal(results))
    if any(result.status == DiagnosticStatus.FAIL for result in results):
        raise click.exceptions.Exit(1)
