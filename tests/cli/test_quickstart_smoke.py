"""Regression smoke tests for the quickstart path using a fake service discovery.

Drives the documented bootstrap flow end to end with fake discovery and a
stubbed composer, asserting it reaches service start with the expected
rendered services.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import pytest
from click.testing import CliRunner

from phlo.cli.infrastructure.container_backend import ContainerInfo
from phlo.plugins.discovery import ServiceDefinition

pytestmark = pytest.mark.core_regression


def _service(name: str, *, default: bool = False) -> ServiceDefinition:
    return ServiceDefinition(
        name=name,
        description=f"{name} service",
        category="core",
        default=default,
    )


class _FakeDiscovery:
    def __init__(
        self,
        services: dict[str, ServiceDefinition],
        *,
        default_names: tuple[str, ...],
    ) -> None:
        self._services = services
        self._default_names = default_names

    def discover(self) -> dict[str, ServiceDefinition]:
        return self._services

    def get_default_services(
        self,
        disabled_services: set[str] | None = None,
    ) -> list[ServiceDefinition]:
        disabled = disabled_services or set()
        return [self._services[name] for name in self._default_names if name not in disabled]

    def get_available_profiles(self) -> set[str]:
        return {svc.profile for svc in self._services.values() if svc.profile}

    def get_services_by_profile(self, profile: str) -> list[ServiceDefinition]:
        return [svc for svc in self._services.values() if svc.profile == profile]

    def resolve_dependencies(
        self,
        services: list[ServiceDefinition],
    ) -> list[ServiceDefinition]:
        return services


class _SmokeComposer:
    def __init__(self, _discovery: _FakeDiscovery) -> None:
        self.generated_service_names: list[str] = []

    def generate_compose(
        self,
        services: list[ServiceDefinition],
        _output_dir: Path,
        **_kwargs: Any,
    ) -> str:
        self.generated_service_names = sorted(service.name for service in services)
        lines = ["services:"]
        for service_name in self.generated_service_names:
            lines.append(f"  {service_name}: {{}}")
        return "\n".join(lines) + "\n"

    def generate_env(
        self,
        _services: list[ServiceDefinition],
        env_overrides: dict[str, str] | None = None,
    ) -> str:
        lines = ["PHLO_ENV=smoke"]
        if env_overrides:
            lines.extend(f"{key}={value}" for key, value in sorted(env_overrides.items()))
        return "\n".join(lines) + "\n"

    def generate_env_local(
        self,
        _services: list[ServiceDefinition],
        env_overrides: dict[str, str] | None = None,
        existing_values: dict[str, str] | None = None,
    ) -> str:
        return "PHLO_SECRET=smoke\n"

    def generate_gitignore(self, _services: list[ServiceDefinition]) -> str:
        return ".env.local\n"

    def copy_service_files(
        self,
        _services: list[ServiceDefinition],
        _output_dir: Path,
    ) -> list[str]:
        return []


def test_documented_quickstart_bootstrap_path_reaches_services_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """The documented init -> services init -> services start path stays wired together."""
    from phlo.cli import main as main_module
    from phlo.cli.commands.services import init as init_module
    from phlo.cli.commands.services import start as start_module
    from phlo.cli.templates import builtin as builtin_templates

    postgres = _service("postgres", default=True)
    prometheus = ServiceDefinition(
        name="prometheus",
        description="prometheus service",
        category="observability",
        default=False,
        profile="observability",
    )
    fake_discovery = _FakeDiscovery(
        {postgres.name: postgres, prometheus.name: prometheus},
        default_names=(postgres.name,),
    )
    docker_calls: list[list[str]] = []

    def _fake_run_command(cmd: list[str], check=False, capture_output=False) -> CompletedProcess:
        docker_calls.append(cmd)
        return CompletedProcess(args=cmd, returncode=0)

    class _FakeBackend:
        def list_project_containers(self, project_name: str):
            return [
                ContainerInfo(
                    service="postgres",
                    name=f"{project_name}-postgres-1",
                    state="running",
                    labels={"com.docker.compose.service": "postgres"},
                    ports="",
                )
            ]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(builtin_templates, "_build_env_example_content", lambda: "PHLO_SECRET=\n")
    monkeypatch.setattr(init_module, "ServiceDiscovery", lambda: fake_discovery)
    monkeypatch.setattr(init_module, "ComposeGenerator", _SmokeComposer)
    monkeypatch.setattr(start_module, "ServiceDiscovery", lambda: fake_discovery)
    monkeypatch.setattr(start_module, "get_project_name", lambda: "demo")
    monkeypatch.setattr(start_module, "compose_base_cmd", lambda **_kwargs: ["docker", "compose"])
    monkeypatch.setattr(
        start_module, "select_project_container_backend", lambda **_kwargs: _FakeBackend()
    )
    monkeypatch.setattr(start_module, "run_command", _fake_run_command)
    monkeypatch.setattr(start_module, "require_container_backend", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        start_module, "_emit_service_lifecycle_events", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(start_module, "_run_service_hooks", lambda *args, **kwargs: None)

    runner = CliRunner()
    init_result = runner.invoke(main_module.init, ["demo", "--template", "minimal"])
    assert init_result.exit_code == 0, init_result.output
    assert "uv pip install -e ." in init_result.output
    assert "phlo services init" in init_result.output
    assert "phlo services start" in init_result.output
    assert "phlo doctor" in init_result.output

    project_dir = tmp_path / "demo"
    monkeypatch.chdir(project_dir)
    services_init_result = runner.invoke(init_module.init_cmd, ["--no-dev"])
    assert services_init_result.exit_code == 0, services_init_result.output
    assert (project_dir / "phlo.yaml").exists()
    assert (project_dir / ".phlo" / "docker-compose.yml").exists()
    assert (project_dir / ".phlo" / ".env").exists()
    assert (project_dir / ".phlo" / ".env.local").exists()
    compose_content = (project_dir / ".phlo" / "docker-compose.yml").read_text()
    assert "postgres" in compose_content
    assert "prometheus" not in compose_content

    start_result = runner.invoke(start_module.start_cmd, [])
    assert start_result.exit_code == 0, start_result.output
    assert docker_calls == [["docker", "compose", "up", "-d"]]
    assert "Phlo infrastructure started." in start_result.output
    assert "Services running: postgres" in start_result.output

    artifact_dir = os.environ.get("PHLO_QUICKSTART_SMOKE_ARTIFACT_DIR")
    if artifact_dir:
        destination = Path(artifact_dir)
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(project_dir / "phlo.yaml", destination / "phlo.yaml")
        shutil.copytree(
            project_dir / ".phlo",
            destination / ".phlo",
            dirs_exist_ok=True,
        )
