"""Tests for "phlo services list".

Container status must come from the backend listing with graceful handling of
backend, config, and discovery failures; running optional services appear by
default, declared ports render correctly, and long names align.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from phlo.cli.infrastructure.container_backend import ContainerInfo
from phlo.plugins.discovery import ServiceDefinition
from tests.helpers import FakeDiscovery


def test_services_list_uses_backend_container_listing(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from phlo.cli.commands.services import list as list_module

    class ServiceFakeDiscovery(FakeDiscovery):
        def discover(self) -> dict[str, ServiceDefinition]:
            return {
                "postgres": ServiceDefinition(
                    name="postgres",
                    description="Postgres",
                    category="metadata",
                )
            }

    class FakeBackend:
        name = "podman"

        def list_project_containers(self, project_name: str):
            return [
                ContainerInfo(
                    service="postgres",
                    name=f"{project_name}-postgres-1",
                    state="running",
                    labels={"com.docker.compose.service": "postgres"},
                    ports="0.0.0.0:5432->5432/tcp",
                )
            ]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(list_module, "ServiceDiscovery", ServiceFakeDiscovery)
    monkeypatch.setattr(list_module, "get_project_name", lambda: "demo")
    monkeypatch.setattr(
        list_module, "select_project_container_backend", lambda **_kwargs: FakeBackend()
    )

    result = CliRunner().invoke(list_module.list_cmd, ["--json", "--backend", "podman"])

    assert result.exit_code == 0
    assert '"running": true' in result.output


def test_services_list_shows_running_optional_services_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from phlo.cli.commands.services import list as list_module

    class ServiceFakeDiscovery(FakeDiscovery):
        def discover(self) -> dict[str, ServiceDefinition]:
            return {
                "phlo-api": ServiceDefinition(
                    name="phlo-api",
                    description="API",
                    category="api",
                    profile="api",
                    default=False,
                )
            }

    class FakeBackend:
        name = "docker"

        def list_project_containers(self, project_name: str):
            return [
                ContainerInfo(
                    service="phlo-api",
                    name=f"{project_name}-phlo-api-1",
                    state="running",
                    labels={"com.docker.compose.service": "phlo-api"},
                    ports="0.0.0.0:4000->4000/tcp",
                )
            ]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(list_module, "ServiceDiscovery", ServiceFakeDiscovery)
    monkeypatch.setattr(list_module, "get_project_name", lambda: "demo")
    monkeypatch.setattr(
        list_module, "select_project_container_backend", lambda **_kwargs: FakeBackend()
    )

    result = CliRunner().invoke(list_module.list_cmd, [])

    assert result.exit_code == 0
    assert "phlo-api" in result.output
    assert "Running" in result.output


def test_services_list_uses_first_declared_port_for_multi_port_services(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from phlo.cli.commands.services import list as list_module

    class ServiceFakeDiscovery(FakeDiscovery):
        def discover(self) -> dict[str, ServiceDefinition]:
            return {
                "clickstack": ServiceDefinition(
                    name="clickstack",
                    description="ClickStack",
                    category="observability",
                    profile="observability",
                    default=False,
                    compose={
                        "ports": [
                            "${CLICKSTACK_PORT:-8080}:8080",
                            "${CLICKSTACK_OTLP_GRPC_PORT:-4317}:4317",
                        ]
                    },
                )
            }

    class FakeBackend:
        name = "docker"

        def list_project_containers(self, project_name: str):
            return [
                ContainerInfo(
                    service="clickstack",
                    name=f"{project_name}-clickstack-1",
                    state="running",
                    labels={"com.docker.compose.service": "clickstack"},
                    ports="0.0.0.0:34317->4317/tcp, 0.0.0.0:38082->8080/tcp",
                )
            ]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(list_module, "ServiceDiscovery", ServiceFakeDiscovery)
    monkeypatch.setattr(list_module, "get_project_name", lambda: "demo")
    monkeypatch.setattr(
        list_module, "select_project_container_backend", lambda **_kwargs: FakeBackend()
    )

    result = CliRunner().invoke(list_module.list_cmd, [])

    assert result.exit_code == 0, result.output
    assert ":38082" in result.output
    assert ":34317" not in result.output


def test_services_list_wraps_config_parse_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from phlo.cli.commands.services import list as list_module

    (tmp_path / "phlo.yaml").write_text("services: [\n")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(list_module.list_cmd, ["--json"])
    assert result.exit_code == 1
    assert "Failed to read" in result.output
    assert "Check YAML syntax and file permissions" in result.output


def test_services_list_wraps_discovery_failures(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from phlo.cli.commands.services import list as list_module

    class FailingDiscovery:
        def discover(self) -> dict[str, ServiceDefinition]:
            raise RuntimeError("discovery blew up")

    monkeypatch.setattr(list_module, "ServiceDiscovery", FailingDiscovery)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(list_module.list_cmd, ["--json"])
    assert result.exit_code == 1
    assert "Failed to discover services." in result.output
    assert "phlo plugins list" in result.output


def test_services_list_handles_backend_status_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from phlo.cli.commands.services import list as list_module

    class EmptyFakeDiscovery(FakeDiscovery):
        def discover(self) -> dict[str, ServiceDefinition]:
            return {}

    monkeypatch.setattr(list_module, "ServiceDiscovery", EmptyFakeDiscovery)
    monkeypatch.setattr(list_module, "get_project_name", lambda: "demo")
    monkeypatch.setattr(
        list_module,
        "select_project_container_backend",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("bad backend")),
    )
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(list_module.list_cmd, ["--json"])
    assert result.exit_code == 0
    assert result.output.strip() == "[]"


def test_services_list_aligns_long_service_names(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from phlo.cli.commands.services import list as list_module

    class ServiceFakeDiscovery(FakeDiscovery):
        def discover(self) -> dict[str, ServiceDefinition]:
            return {
                "postgres": ServiceDefinition(
                    name="postgres",
                    description="Postgres",
                    category="metadata",
                ),
                "postgres-volume-setup": ServiceDefinition(
                    name="postgres-volume-setup",
                    description="Volume setup",
                    category="metadata",
                ),
            }

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(list_module, "ServiceDiscovery", ServiceFakeDiscovery)
    monkeypatch.setattr(list_module, "get_project_name", lambda: "demo")
    monkeypatch.setattr(
        list_module,
        "select_project_container_backend",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("no backend")),
    )

    result = CliRunner().invoke(list_module.list_cmd, [])

    assert result.exit_code == 0
    lines = result.output.splitlines()
    postgres_line = next(line for line in lines if " postgres " in line)
    setup_line = next(line for line in lines if " postgres-volume-setup " in line)
    assert postgres_line.index("Stopped") == setup_line.index("Stopped")
