"""Tests for "phlo services ports".

Port resolution prefers runtime container mappings over configured defaults, and
conflict detection and Traefik routing follow the effective overrides from phlo.yaml
and the shell environment.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from phlo.cli.commands.services import ports as ports_module
from phlo.cli.infrastructure.container_backend import ContainerInfo
from phlo.plugins.discovery import ServiceDefinition
from tests.helpers import FakeDiscovery


def test_parse_compose_port_with_env_var() -> None:
    env_var, container_port = ports_module._parse_compose_port("${POSTGRES_PORT:-5432}:5432")
    assert env_var == "POSTGRES_PORT"
    assert container_port == "5432"


def test_parse_compose_port_with_default_only() -> None:
    env_var, container_port = ports_module._parse_compose_port("${POSTGRES_PORT}:5432")
    assert env_var == "POSTGRES_PORT"
    assert container_port == "5432"


def test_parse_compose_port_no_env() -> None:
    env_var, container_port = ports_module._parse_compose_port("5432:5432")
    assert env_var is None
    assert container_port == "5432"


def test_parse_compose_port_with_host_port() -> None:
    env_var, container_port = ports_module._parse_compose_port("10000:5432")
    assert env_var is None
    assert container_port == "5432"


def test_parse_compose_port_spec_with_host_port() -> None:
    spec = ports_module._parse_compose_port_spec("127.0.0.1:10000:5432")
    assert spec.env_var is None
    assert spec.host_port == "10000"
    assert spec.container_port == "5432"


def test_resolve_env_var_found() -> None:
    env = {"POSTGRES_PORT": "10000"}
    result = ports_module._resolve_env_var("POSTGRES_PORT", env)
    assert result == "10000"


def test_resolve_env_var_not_found() -> None:
    env: dict[str, str] = {}
    result = ports_module._resolve_env_var("POSTGRES_PORT", env)
    assert result is None


def test_resolve_env_var_none() -> None:
    env: dict[str, str] = {}
    result = ports_module._resolve_env_var(None, env)
    assert result is None


def test_detect_conflicts_no_conflicts() -> None:
    mappings = [
        ports_module.PortMapping(
            service="postgres",
            host_port=10000,
            container_port=5432,
            source="env",
            status="Running",
            env_var="POSTGRES_PORT",
        ),
        ports_module.PortMapping(
            service="minio",
            host_port=10001,
            container_port=9000,
            source="env",
            status="Running",
            env_var="MINIO_PORT",
        ),
    ]
    conflicts = ports_module._detect_conflicts(mappings)
    assert conflicts == []


def test_detect_conflicts_with_conflicts() -> None:
    mappings = [
        ports_module.PortMapping(
            service="postgres",
            host_port=10000,
            container_port=5432,
            source="env",
            status="Running",
            env_var="POSTGRES_PORT",
        ),
        ports_module.PortMapping(
            service="trino", host_port=8080, container_port=8080, source="default", status="Running"
        ),
        ports_module.PortMapping(
            service="hasura",
            host_port=8080,
            container_port=8080,
            source="default",
            status="Running",
        ),
    ]
    conflicts = ports_module._detect_conflicts(mappings)
    assert len(conflicts) == 1
    assert (conflicts[0][0], conflicts[0][1], conflicts[0][2]) == ("trino", "hasura", 8080)


def test_detect_conflicts_multiple_services_same_port() -> None:
    mappings = [
        ports_module.PortMapping(
            service="service1",
            host_port=8080,
            container_port=8080,
            source="default",
            status="Running",
        ),
        ports_module.PortMapping(
            service="service2",
            host_port=8080,
            container_port=8080,
            source="default",
            status="Running",
        ),
        ports_module.PortMapping(
            service="service3",
            host_port=8080,
            container_port=8080,
            source="default",
            status="Running",
        ),
    ]
    conflicts = ports_module._detect_conflicts(mappings)
    assert len(conflicts) >= 1


def test_ports_cmd_requires_phlo_dir(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(ports_module.ports_cmd)
    assert result.exit_code == 1
    assert "Error: Phlo services have not been initialized" in result.output
    assert "Missing: .phlo/" in result.output
    assert "Run: phlo services init" in result.output


def test_ports_cmd_handles_no_services(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    (tmp_path / ".phlo").mkdir()
    (tmp_path / "phlo.yaml").write_text("name: test\n")

    class EmptyFakeDiscovery(FakeDiscovery):
        def discover(self) -> dict[str, ServiceDefinition]:
            return {}

    monkeypatch.setattr(ports_module, "ServiceDiscovery", EmptyFakeDiscovery)
    monkeypatch.setattr(ports_module, "get_project_name", lambda: "test")
    monkeypatch.setattr(ports_module, "_get_running_container_ports", lambda *_args: {})
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(ports_module.ports_cmd)
    assert result.exit_code == 0
    assert "No port mappings found" in result.output


def test_ports_cmd_json_output(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    (tmp_path / ".phlo").mkdir()
    (tmp_path / "phlo.yaml").write_text("name: test\n")

    class EmptyFakeDiscovery(FakeDiscovery):
        def discover(self) -> dict[str, ServiceDefinition]:
            return {}

    monkeypatch.setattr(ports_module, "ServiceDiscovery", EmptyFakeDiscovery)
    monkeypatch.setattr(ports_module, "get_project_name", lambda: "test")
    monkeypatch.setattr(ports_module, "_get_running_container_ports", lambda *_args: {})
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(ports_module.ports_cmd, ["--json", "--all"])
    assert result.exit_code == 0
    assert json.loads(result.output)["data"] == []


def test_ports_cmd_json_output_is_payload_only(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    (tmp_path / ".phlo").mkdir()
    (tmp_path / "phlo.yaml").write_text("name: test\n")
    service = ServiceDefinition(
        name="postgres",
        description="PostgreSQL",
        compose={"ports": ["${POSTGRES_PORT:-5432}:5432"]},
    )

    class PortFakeDiscovery(FakeDiscovery):
        def discover(self) -> dict[str, ServiceDefinition]:
            return {"postgres": service}

    monkeypatch.setattr(ports_module, "ServiceDiscovery", PortFakeDiscovery)
    monkeypatch.setattr(ports_module, "get_project_name", lambda: "test")
    monkeypatch.setattr(ports_module, "_get_running_container_ports", lambda *_args: {})
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(ports_module.ports_cmd, ["--json", "--all"])

    assert result.exit_code == 0
    assert isinstance(json.loads(result.output)["data"], list)
    assert '"service": "postgres"' in result.output


def test_get_running_container_ports_uses_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeBackend:
        name = "podman"

        def list_project_containers(self, project_name: str):
            return [
                ContainerInfo(
                    service="postgres",
                    name=f"{project_name}-postgres-1",
                    state="running",
                    labels={"com.docker.compose.service": "postgres"},
                    ports="0.0.0.0:15432->5432/tcp",
                )
            ]

    monkeypatch.setattr(
        ports_module, "select_project_container_backend", lambda **_kwargs: FakeBackend()
    )

    containers = ports_module._get_running_container_ports("demo", "podman")

    assert containers["postgres"]["status"] == "running"
    assert containers["postgres"]["ports"] == [
        {
            "host_port": "15432",
            "host_ip": "0.0.0.0",
            "container_port": "5432/tcp",
        }
    ]


def test_get_service_ports_with_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {"POSTGRES_PORT": "10000"}
    running_containers = {"postgres": {"status": "running", "ports": []}}
    show_all = False

    service = ServiceDefinition(
        name="postgres",
        description="PostgreSQL",
        compose={"ports": ["${POSTGRES_PORT:-5432}:5432"]},
    )

    ports = ports_module._get_service_ports(service, env, running_containers, show_all)
    assert len(ports) == 1
    assert ports[0].host_port == 10000
    assert ports[0].container_port == 5432
    assert ports[0].source == "env"
    assert ports[0].env_var == "POSTGRES_PORT"


def test_get_service_ports_with_default(monkeypatch: pytest.MonkeyPatch) -> None:
    env: dict[str, str] = {}
    running_containers: dict[str, dict] = {}
    show_all = True

    service = ServiceDefinition(
        name="postgres",
        description="PostgreSQL",
        compose={"ports": ["${POSTGRES_PORT:-5432}:5432"]},
    )

    ports = ports_module._get_service_ports(service, env, running_containers, show_all)
    assert len(ports) == 1
    assert ports[0].host_port == 5432
    assert ports[0].container_port == 5432
    assert ports[0].source == "default"


def test_get_service_ports_with_explicit_compose_host_port() -> None:
    env: dict[str, str] = {}
    running_containers: dict[str, dict] = {}
    show_all = True

    service = ServiceDefinition(
        name="postgres",
        description="PostgreSQL",
        compose={"ports": ["15432:5432"]},
    )

    ports = ports_module._get_service_ports(service, env, running_containers, show_all)
    assert len(ports) == 1
    assert ports[0].host_port == 15432
    assert ports[0].source == "compose"


def test_get_service_ports_prefers_runtime_mapping_over_default() -> None:
    env: dict[str, str] = {}
    running_containers = {
        "postgres": {
            "status": "running",
            "ports": [{"host_port": "15432", "container_port": "5432/tcp"}],
        }
    }
    show_all = False

    service = ServiceDefinition(
        name="postgres",
        description="PostgreSQL",
        compose={"ports": ["${POSTGRES_PORT:-5432}:5432"]},
    )

    ports = ports_module._get_service_ports(service, env, running_containers, show_all)
    # Reporting precedence: a live runtime binding wins over the env-var
    # override, which wins over the compose default.
    assert len(ports) == 1
    assert ports[0].host_port == 15432
    assert ports[0].source == "runtime"


def test_load_environment_merges_config_and_shell_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / ".env").write_text("POSTGRES_PORT=5432\nMINIO_PORT=9000\n")
    (phlo_dir / ".env.local").write_text("MINIO_PORT=9001\n")
    monkeypatch.setenv("POSTGRES_PORT", "15432")

    # Merge precedence: shell env beats .env.local, which beats .env;
    # phlo.yaml env values only fill ports nothing else defines.
    env = ports_module._load_environment(
        phlo_dir,
        {"env": {"POSTGRES_PORT": 15431, "TRINO_PORT": 18080}},
    )

    assert env["POSTGRES_PORT"] == "15432"
    assert env["MINIO_PORT"] == "9001"
    assert env["TRINO_PORT"] == "18080"


def test_get_service_ports_no_ports() -> None:
    env: dict[str, str] = {}
    running_containers: dict[str, dict] = {}
    show_all = False

    service = ServiceDefinition(
        name="postgres",
        description="PostgreSQL",
        compose={},
    )

    ports = ports_module._get_service_ports(service, env, running_containers, show_all)
    assert ports == []


def test_get_service_ports_hides_stopped_configured_ports_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env: dict[str, str] = {}
    running_containers: dict[str, dict] = {}
    show_all = False

    service = ServiceDefinition(
        name="postgres",
        description="PostgreSQL",
        compose={"ports": ["${POSTGRES_PORT:-5432}:5432"]},
    )

    ports = ports_module._get_service_ports(service, env, running_containers, show_all)
    assert ports == []


def test_get_service_ports_shows_stopped_configured_ports_with_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env: dict[str, str] = {}
    running_containers: dict[str, dict] = {}
    show_all = True

    service = ServiceDefinition(
        name="postgres",
        description="PostgreSQL",
        compose={"ports": ["${POSTGRES_PORT:-5432}:5432"]},
    )

    ports = ports_module._get_service_ports(service, env, running_containers, show_all)
    assert len(ports) == 1
    assert ports[0].host_port == 5432
    assert ports[0].status == "Stopped"


def test_get_service_routes_requires_active_traefik() -> None:
    service = ServiceDefinition(
        name="dagster",
        description="Dagster",
        compose={
            "labels": {
                "traefik.enable": "true",
                "traefik.http.routers.dagster.rule": "Host(`dagster.${TRAEFIK_DOMAIN:-phlo.localhost}`)",
                "traefik.http.services.dagster.loadbalancer.server.port": "3000",
            }
        },
    )

    routes = ports_module._get_service_routes({"dagster": service}, None)
    assert routes == {}


def test_get_active_traefik_context_uses_runtime_or_override_port() -> None:
    services = {
        "traefik": ServiceDefinition(
            name="traefik",
            description="Traefik",
            compose={"ports": ["${TRAEFIK_HTTP_PORT:-80}:80"]},
        )
    }
    env = {"TRAEFIK_HTTP_PORT": "80", "TRAEFIK_DOMAIN": "phlo.localhost"}
    running_containers = {
        "traefik": {
            "status": "running",
            "ports": [{"host_port": "8088", "container_port": "80/tcp"}],
        }
    }

    context = ports_module._get_active_traefik_context(
        services,
        env,
        running_containers,
        disabled_services=set(),
        service_overrides={"traefik": {"ports": ["8088:80"]}},
    )

    assert context == ports_module.TraefikContext(domain="phlo.localhost", host_port=8088)


def test_get_service_routes_use_resolved_traefik_port() -> None:
    service = ServiceDefinition(
        name="dagster",
        description="Dagster",
        compose={
            "labels": {
                "traefik.enable": "true",
                "traefik.http.routers.dagster.rule": "Host(`dagster.${TRAEFIK_DOMAIN:-phlo.localhost}`)",
                "traefik.http.services.dagster.loadbalancer.server.port": "3000",
            }
        },
    )

    routes = ports_module._get_service_routes(
        {"dagster": service},
        ports_module.TraefikContext(domain="phlo.localhost", host_port=8088),
    )
    assert routes == {"dagster": {"3000": "http://dagster.phlo.localhost:8088"}}


def test_get_service_routes_support_traefik_dashboard_api_internal() -> None:
    service = ServiceDefinition(
        name="traefik",
        description="Traefik",
        compose={
            "labels": {
                "traefik.enable": "true",
                "traefik.http.routers.traefik.rule": (
                    "Host(`traefik.${TRAEFIK_DOMAIN:-phlo.localhost}`)"
                ),
                "traefik.http.routers.traefik.service": "api@internal",
            }
        },
    )

    routes = ports_module._get_service_routes(
        {"traefik": service},
        ports_module.TraefikContext(domain="phlo.localhost", host_port=80),
    )
    assert routes == {"traefik": {"80": "http://traefik.phlo.localhost"}}


def test_ports_cmd_uses_phlo_yaml_env_and_service_port_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    (tmp_path / ".phlo").mkdir()
    (tmp_path / "phlo.yaml").write_text(
        """
name: test
env:
  POSTGRES_PORT: 15432
services:
  postgres:
    ports:
      - "16432:5432"
"""
    )

    class PostgresFakeDiscovery(FakeDiscovery):
        def discover(self) -> dict[str, ServiceDefinition]:
            return {
                "postgres": ServiceDefinition(
                    name="postgres",
                    description="PostgreSQL",
                    compose={"ports": ["${POSTGRES_PORT:-5432}:5432"]},
                )
            }

    monkeypatch.setattr(ports_module, "ServiceDiscovery", PostgresFakeDiscovery)
    monkeypatch.setattr(ports_module, "get_project_name", lambda: "test")
    monkeypatch.setattr(ports_module, "_get_running_container_ports", lambda *_args: {})
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(ports_module.ports_cmd, ["--all", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["data"] == [
        {
            "service": "postgres",
            "host_port": 16432,
            "container_port": 5432,
            "source": "compose",
            "status": "stopped",
            "env_var": None,
            "url": None,
        }
    ]


def test_ports_cmd_does_not_advertise_urls_without_running_traefik(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    (tmp_path / ".phlo").mkdir()
    (tmp_path / "phlo.yaml").write_text("name: test\n")

    class DagsterFakeDiscovery(FakeDiscovery):
        def discover(self) -> dict[str, ServiceDefinition]:
            return {
                "dagster": ServiceDefinition(
                    name="dagster",
                    description="Dagster",
                    compose={
                        "ports": ["${DAGSTER_PORT:-3000}:3000"],
                        "labels": {
                            "traefik.enable": "true",
                            "traefik.http.routers.dagster.rule": (
                                "Host(`dagster.${TRAEFIK_DOMAIN:-phlo.localhost}`)"
                            ),
                            "traefik.http.services.dagster.loadbalancer.server.port": "3000",
                        },
                    },
                )
            }

    monkeypatch.setattr(ports_module, "ServiceDiscovery", DagsterFakeDiscovery)
    monkeypatch.setattr(ports_module, "get_project_name", lambda: "test")
    monkeypatch.setattr(ports_module, "_get_running_container_ports", lambda *_args: {})
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(ports_module.ports_cmd, ["--all", "--json"])
    assert result.exit_code == 0
    assert '"url": null' in result.output


def test_ports_cmd_uses_effective_traefik_override_port_in_urls(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    (tmp_path / ".phlo").mkdir()
    (tmp_path / "phlo.yaml").write_text(
        """
name: test
services:
  traefik:
    ports:
      - "8088:80"
"""
    )

    class TraefikDagsterFakeDiscovery(FakeDiscovery):
        def discover(self) -> dict[str, ServiceDefinition]:
            return {
                "traefik": ServiceDefinition(
                    name="traefik",
                    description="Traefik",
                    compose={"ports": ["${TRAEFIK_HTTP_PORT:-80}:80"]},
                ),
                "dagster": ServiceDefinition(
                    name="dagster",
                    description="Dagster",
                    compose={
                        "ports": ["${DAGSTER_PORT:-3000}:3000"],
                        "labels": {
                            "traefik.enable": "true",
                            "traefik.http.routers.dagster.rule": (
                                "Host(`dagster.${TRAEFIK_DOMAIN:-phlo.localhost}`)"
                            ),
                            "traefik.http.services.dagster.loadbalancer.server.port": "3000",
                        },
                    },
                ),
            }

    monkeypatch.setattr(ports_module, "ServiceDiscovery", TraefikDagsterFakeDiscovery)
    monkeypatch.setattr(ports_module, "get_project_name", lambda: "test")
    monkeypatch.setattr(
        ports_module,
        "_get_running_container_ports",
        lambda *_args: {
            "traefik": {
                "status": "running",
                "ports": [{"host_port": "8088", "container_port": "80/tcp"}],
            },
            "dagster": {
                "status": "running",
                "ports": [{"host_port": "3000", "container_port": "3000/tcp"}],
            },
        },
    )
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(ports_module.ports_cmd, ["--json"])
    assert result.exit_code == 0
    assert '"url": "http://dagster.phlo.localhost:8088"' in result.output
