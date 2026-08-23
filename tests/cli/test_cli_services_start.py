"""Tests for "phlo services start": profile selection, preflight plans, and backend interaction.

Covers profile/target resolution and dependency expansion, the preflight
contract (unknown profiles or targets fail fast; port collisions and invalid
or missing required env abort before the backend runs), setup-companion
matching, native interpreter selection, and polished errors without tracebacks.
"""

from __future__ import annotations

from subprocess import CompletedProcess

import pytest
from click.testing import CliRunner

from phlo.cli.commands.services.planner import StartPreflightPlan
from phlo.cli.commands.services.utils import get_profile_service_names
from phlo.cli.infrastructure.container_backend import ContainerInfo
from phlo.plugins.discovery import ServiceDefinition
from tests.helpers import FakeDiscovery, _service


def test_get_profile_service_names_returns_profile_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prometheus = _service("prometheus", profile="observability")
    grafana = _service("grafana", profile="observability")
    loki = _service("loki", profile="observability")
    hasura = _service("hasura", profile="api")
    postgres = _service("postgres", default=True)

    class ProfileFakeDiscovery(FakeDiscovery):
        def get_services_by_profile(self, profile: str) -> list[ServiceDefinition]:
            all_services = [prometheus, grafana, loki, hasura, postgres]
            return [s for s in all_services if s.profile == profile]

    monkeypatch.setattr(
        "phlo.plugins.discovery.ServiceDiscovery",
        ProfileFakeDiscovery,
    )

    result = get_profile_service_names(("observability",))
    assert sorted(result) == [
        "alloy",
        "clickstack",
        "grafana",
        "loki",
        "postgres-exporter",
        "prometheus",
    ]

    result = get_profile_service_names(("api",))
    assert sorted(result) == ["hasura", "observatory", "phlo-api", "postgrest"]

    result = get_profile_service_names(("observability", "api"))
    assert sorted(result) == [
        "alloy",
        "clickstack",
        "grafana",
        "hasura",
        "loki",
        "observatory",
        "phlo-api",
        "postgres-exporter",
        "postgrest",
        "prometheus",
    ]

    result = get_profile_service_names(())
    assert result == []


def test_run_service_hooks_uses_sys_executable_when_project_venv_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from phlo.cli.commands.services import utils as services_utils

    service = ServiceDefinition(
        name="dagster",
        description="Dagster",
        category="orchestration",
        hooks={"post_start": [{"command": ["python3", "-m", "phlo_dbt.hooks", "compile"]}]},
    )

    class ServiceFakeDiscovery(FakeDiscovery):
        def get_service(self, name: str):
            return service if name == "dagster" else None

    calls: list[list[str]] = []

    def _fake_run_command(cmd, **_kwargs):
        calls.append(list(cmd))
        return CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("phlo.plugins.discovery.ServiceDiscovery", ServiceFakeDiscovery)
    monkeypatch.setattr(services_utils, "run_command", _fake_run_command)
    monkeypatch.setattr(services_utils.sys, "executable", "/usr/local/bin/current-python")

    services_utils._run_service_hooks(
        "post_start",
        ["dagster"],
        project_name="demo",
        project_root=tmp_path,
    )

    assert calls
    assert calls[0][0] == "/usr/local/bin/current-python"


def test_run_service_hooks_prefers_phlo_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from phlo.cli.commands.services import utils as services_utils

    service = ServiceDefinition(
        name="dagster",
        description="Dagster",
        category="orchestration",
        hooks={"post_start": [{"command": ["python", "-m", "phlo_dbt.hooks", "compile"]}]},
    )

    class ServiceFakeDiscovery(FakeDiscovery):
        def get_service(self, name: str):
            return service if name == "dagster" else None

    calls: list[list[str]] = []

    def _fake_run_command(cmd, **_kwargs):
        calls.append(list(cmd))
        return CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("phlo.plugins.discovery.ServiceDiscovery", ServiceFakeDiscovery)
    monkeypatch.setattr(services_utils, "run_command", _fake_run_command)

    services_utils._run_service_hooks(
        "post_start",
        ["dagster"],
        project_name="demo",
        project_root=tmp_path,
    )

    assert calls
    assert calls[0][0] == services_utils.sys.executable


def test_services_start_rejects_unknown_profile(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from phlo.cli.commands.services import start as start_module

    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / "docker-compose.yml").write_text("services:\n  postgres: {}\n")

    class ProfilesFakeDiscovery(FakeDiscovery):
        def get_available_profiles(self) -> set[str]:
            return {"api", "observability"}

    def _unexpected_call(*_args, **_kwargs):
        raise AssertionError("Docker command path should not execute for invalid profiles")

    monkeypatch.setattr(start_module, "ensure_phlo_dir", lambda: phlo_dir)
    monkeypatch.setattr(start_module, "ServiceDiscovery", ProfilesFakeDiscovery)
    monkeypatch.setattr(start_module, "run_command", _unexpected_call)
    monkeypatch.setattr(start_module, "require_container_backend", _unexpected_call)

    result = CliRunner().invoke(start_module.start_cmd, ["--profile", "not-a-profile"])

    assert result.exit_code != 0
    assert "Invalid profile: not-a-profile." in result.output
    assert "Valid profile options: api, observability" in result.output


def test_services_start_missing_compose_is_polished_without_log_leak(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from phlo.cli.commands.services import start as start_module

    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(start_module, "ensure_phlo_dir", lambda: phlo_dir)
    monkeypatch.setattr(start_module, "get_project_name", lambda: "demo")
    monkeypatch.setattr(start_module.logger, "error", lambda *_args, **_kwargs: None)

    result = CliRunner().invoke(start_module.start_cmd, [])

    assert result.exit_code != 0
    assert "services_start_missing_compose_file" not in result.output
    assert "Error: Phlo services have not been initialized" in result.output
    assert "Missing: .phlo/docker-compose.yml" in result.output
    assert "Run: phlo services init" in result.output


def test_services_start_uses_podman_backend(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from phlo.cli.commands.services import start as start_module

    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / "docker-compose.yml").write_text("services:\n  postgres: {}\n")
    (phlo_dir / ".env").write_text("")
    (tmp_path / "phlo.yaml").write_text("name: demo\n")
    monkeypatch.chdir(tmp_path)

    calls: list[list[str]] = []

    def _fake_run_command(cmd: list[str], check=False, capture_output=False) -> CompletedProcess:
        calls.append(cmd)
        return CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(start_module, "require_container_backend", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(start_module, "get_project_name", lambda: "demo")
    monkeypatch.setattr(start_module, "run_command", _fake_run_command)
    monkeypatch.setattr(
        start_module, "_emit_service_lifecycle_events", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(start_module, "_run_service_hooks", lambda *args, **kwargs: None)

    result = CliRunner().invoke(start_module.start_cmd, ["--backend", "podman"])

    assert result.exit_code == 0, result.output
    assert calls
    assert calls[0][:2] == ["podman", "compose"]


def test_services_start_uses_profile_targets_without_default_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from phlo.cli.commands.services import start as start_module

    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / "docker-compose.yml").write_text(
        "services:\n  postgres: {}\n  prometheus: {}\n",
    )

    class ProfileTargetFakeDiscovery(FakeDiscovery):
        def get_available_profiles(self) -> set[str]:
            return {"observability"}

        def discover(self) -> dict[str, ServiceDefinition]:
            return {"prometheus": _service("prometheus")}

        def resolve_dependencies(
            self, services: list[ServiceDefinition]
        ) -> list[ServiceDefinition]:
            return services

    profile_calls: list[tuple[str, ...]] = []
    docker_calls: list[list[str]] = []

    def _fake_get_profile_service_names(profiles: tuple[str, ...]) -> list[str]:
        profile_calls.append(profiles)
        return ["prometheus"]

    def _fake_run_command(cmd: list[str], check=False, capture_output=False) -> CompletedProcess:
        docker_calls.append(cmd)
        return CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(start_module, "ensure_phlo_dir", lambda: phlo_dir)
    monkeypatch.setattr(start_module, "ServiceDiscovery", ProfileTargetFakeDiscovery)
    monkeypatch.setattr(start_module, "get_project_name", lambda: "demo-project")
    monkeypatch.setattr(start_module, "get_profile_service_names", _fake_get_profile_service_names)
    monkeypatch.setattr(start_module, "compose_base_cmd", lambda **_kwargs: ["docker", "compose"])
    monkeypatch.setattr(start_module, "run_command", _fake_run_command)
    monkeypatch.setattr(start_module, "require_container_backend", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        start_module, "_emit_service_lifecycle_events", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(start_module, "_run_service_hooks", lambda *args, **kwargs: None)

    result = CliRunner().invoke(start_module.start_cmd, ["--profile", "observability"])

    assert result.exit_code == 0
    assert profile_calls == [("observability",)]
    assert docker_calls
    assert "prometheus" in docker_calls[0]
    assert "postgres" not in docker_calls[0]


def test_services_start_preflights_env_local_port_collisions(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from phlo.cli.commands.services import start as start_module

    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / ".env").write_text("DAGSTER_PORT=3000\n")
    (phlo_dir / ".env.local").write_text("DAGSTER_PORT=3300\n")
    compose_file = phlo_dir / "docker-compose.yml"
    compose_file.write_text(
        "services:\n  dagster:\n    ports:\n      - ${DAGSTER_PORT:-3000}:3000\n"
    )

    class FakeBackend:
        def list_project_containers(self, project_name: str):
            return []

    monkeypatch.setattr(
        start_module, "select_project_container_backend", lambda **_kwargs: FakeBackend()
    )
    monkeypatch.setattr(start_module, "_is_host_port_available", lambda port: port != 3300)

    with pytest.raises(Exception) as exc_info:
        start_module._preflight_requested_host_ports(
            plan=StartPreflightPlan(
                phlo_dir=phlo_dir,
                compose_file=compose_file,
                project_root=tmp_path,
                project_name="demo",
                service_names=["dagster"],
                backend_name=None,
            ),
        )

    assert "dagster -> 3300 (DAGSTER_PORT)" in str(exc_info.value)


def test_services_start_preflights_invalid_env_port_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from phlo.cli.commands.services import start as start_module

    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / ".env").write_text("POSTGRES_PORT=not-a-port\n")
    compose_file = phlo_dir / "docker-compose.yml"
    compose_file.write_text(
        "services:\n  postgres:\n    ports:\n      - ${POSTGRES_PORT:-5432}:5432\n"
    )

    class FakeBackend:
        def list_project_containers(self, project_name: str):
            return []

    monkeypatch.setattr(
        start_module, "select_project_container_backend", lambda **_kwargs: FakeBackend()
    )
    monkeypatch.setattr(
        start_module,
        "_is_host_port_available",
        lambda _port: (_ for _ in ()).throw(AssertionError("port bind should not run")),
    )

    with pytest.raises(Exception) as exc_info:
        start_module._preflight_requested_host_ports(
            plan=StartPreflightPlan(
                phlo_dir=phlo_dir,
                compose_file=compose_file,
                project_root=tmp_path,
                project_name="demo",
                service_names=["postgres"],
                backend_name=None,
            ),
        )

    assert "invalid host port value" in str(exc_info.value)
    assert "postgres -> not-a-port (POSTGRES_PORT)" in str(exc_info.value)


def test_services_start_preflight_skips_already_running_project_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from phlo.cli.commands.services import start as start_module

    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    compose_file = phlo_dir / "docker-compose.yml"
    compose_file.write_text(
        "services:\n  dagster:\n    ports:\n      - ${DAGSTER_PORT:-3000}:3000\n"
    )

    class FakeBackend:
        def list_project_containers(self, project_name: str):
            return [
                ContainerInfo(
                    service="dagster",
                    name=f"{project_name}-dagster-1",
                    state="running",
                    labels={"com.docker.compose.service": "dagster"},
                    ports="0.0.0.0:3000->3000/tcp",
                )
            ]

    monkeypatch.setattr(
        start_module, "select_project_container_backend", lambda **_kwargs: FakeBackend()
    )
    monkeypatch.setattr(start_module, "_is_host_port_available", lambda _port: False)

    # A host port held by one of this project's own running containers is not
    # a conflict; the preflight must only reject foreign listeners so restarts
    # of an already-running stack succeed.
    start_module._preflight_requested_host_ports(
        plan=StartPreflightPlan(
            phlo_dir=phlo_dir,
            compose_file=compose_file,
            project_root=tmp_path,
            project_name="demo",
            service_names=["dagster"],
            backend_name=None,
        ),
    )


def test_services_start_includes_setup_companions_for_explicit_targets(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from phlo.cli.commands.services import start as start_module

    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / "docker-compose.yml").write_text(
        "services:\n  rustfs: {}\n  rustfs-setup: {}\n  rustfs-volume-setup: {}\n",
    )

    rustfs_volume_setup = _service("rustfs-volume-setup")
    rustfs = _service("rustfs", depends_on=["rustfs-volume-setup"])
    rustfs_setup = _service("rustfs-setup", depends_on=["rustfs"])

    class SetupCompanionFakeDiscovery(FakeDiscovery):
        def discover(self) -> dict[str, ServiceDefinition]:
            return {
                rustfs_volume_setup.name: rustfs_volume_setup,
                rustfs.name: rustfs,
                rustfs_setup.name: rustfs_setup,
            }

        def resolve_dependencies(
            self, services: list[ServiceDefinition]
        ) -> list[ServiceDefinition]:
            names = {service.name for service in services}
            ordered = [rustfs_volume_setup, rustfs, rustfs_setup]
            return [service for service in ordered if service.name in names]

        def get_available_profiles(self) -> set[str]:
            return set()

    docker_calls: list[list[str]] = []

    def _fake_run_command(cmd: list[str], check=False, capture_output=False) -> CompletedProcess:
        docker_calls.append(cmd)
        return CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(start_module, "ensure_phlo_dir", lambda: phlo_dir)
    monkeypatch.setattr(start_module, "ServiceDiscovery", SetupCompanionFakeDiscovery)
    monkeypatch.setattr(start_module, "get_project_name", lambda: "demo-project")
    monkeypatch.setattr(start_module, "compose_base_cmd", lambda **_kwargs: ["docker", "compose"])
    monkeypatch.setattr(start_module, "run_command", _fake_run_command)
    monkeypatch.setattr(start_module, "require_container_backend", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        start_module, "_emit_service_lifecycle_events", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(start_module, "_run_service_hooks", lambda *args, **kwargs: None)

    result = CliRunner().invoke(start_module.start_cmd, ["--service", "rustfs"])

    assert result.exit_code == 0
    assert docker_calls
    assert docker_calls[0][-3:] == ["rustfs-volume-setup", "rustfs", "rustfs-setup"]


def test_services_start_builds_preflight_plan_for_selected_services(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from phlo.cli.commands.services import start as start_module

    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / ".env").write_text("", encoding="utf-8")
    (phlo_dir / "docker-compose.yml").write_text(
        "services:\n  postgres:\n    image: postgres:16\n",
        encoding="utf-8",
    )
    postgres = _service("postgres", default=True)
    captured: dict[str, object] = {}

    class PostgresFakeDiscovery(FakeDiscovery):
        def discover(self) -> dict[str, ServiceDefinition]:
            return {postgres.name: postgres}

        def get_available_profiles(self) -> set[str]:
            return set()

    def _fake_run_command(cmd: list[str], check=False, capture_output=False) -> CompletedProcess:
        return CompletedProcess(args=cmd, returncode=0)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(start_module, "ensure_phlo_dir", lambda: phlo_dir)
    monkeypatch.setattr(start_module, "ServiceDiscovery", PostgresFakeDiscovery)
    monkeypatch.setattr(start_module, "get_project_name", lambda: "demo")
    monkeypatch.setattr(start_module, "compose_base_cmd", lambda **_kwargs: ["docker", "compose"])
    monkeypatch.setattr(start_module, "require_container_backend", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        start_module,
        "_preflight_requested_host_ports",
        lambda **kwargs: captured.update(kwargs),
    )
    monkeypatch.setattr(start_module, "_preflight_required_env_vars", lambda **_kwargs: None)
    monkeypatch.setattr(start_module, "run_command", _fake_run_command)
    monkeypatch.setattr(
        start_module, "_emit_service_lifecycle_events", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(start_module, "_run_service_hooks", lambda *args, **kwargs: None)

    result = CliRunner().invoke(start_module.start_cmd, ["--service", "postgres"])

    assert result.exit_code == 0
    plan = captured["plan"]
    assert isinstance(plan, StartPreflightPlan)
    assert plan.service_names == ["postgres"]
    assert plan.compose_file.name == "docker-compose.yml"
    assert plan.project_name == "demo"
    assert plan.backend_name is None


def test_services_start_preflights_required_env_for_selected_services(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from phlo.cli.commands.services import start as start_module

    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / ".env").write_text("OAUTH2_PROXY_CLIENT_ID=\n")
    (phlo_dir / ".env.local").write_text("")
    (tmp_path / "phlo.yaml").write_text("name: demo\n")
    (phlo_dir / "docker-compose.yml").write_text("services:\n  oauth2-proxy: {}\n")

    oauth = ServiceDefinition(
        name="oauth2-proxy",
        description="OAuth proxy",
        category="auth",
        profile="proxy",
        env_vars={
            "OAUTH2_PROXY_CLIENT_ID": {"description": "Client ID"},
            "OAUTH2_PROXY_CLIENT_SECRET": {"description": "Client secret"},
            "OAUTH2_PROXY_PROVIDER": {"default": "oidc", "description": "Provider"},
        },
    )

    class ProxyFakeDiscovery(FakeDiscovery):
        def get_available_profiles(self) -> set[str]:
            return {"proxy"}

        def discover(self) -> dict[str, ServiceDefinition]:
            return {oauth.name: oauth}

    def _unexpected_call(*_args, **_kwargs):
        raise AssertionError("Docker should not run when required env is missing")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(start_module, "ensure_phlo_dir", lambda: phlo_dir)
    monkeypatch.setattr(start_module, "ServiceDiscovery", ProxyFakeDiscovery)
    monkeypatch.setattr(start_module, "get_project_name", lambda: "demo")
    monkeypatch.setattr(start_module, "get_profile_service_names", lambda _profiles: [oauth.name])
    monkeypatch.setattr(start_module, "require_container_backend", _unexpected_call)
    monkeypatch.setattr(start_module, "run_command", _unexpected_call)

    result = CliRunner().invoke(start_module.start_cmd, ["--profile", "proxy"])

    assert result.exit_code != 0
    assert "required environment values are missing" in result.output
    assert "oauth2-proxy: OAUTH2_PROXY_CLIENT_ID" in result.output
    assert "oauth2-proxy: OAUTH2_PROXY_CLIENT_SECRET" in result.output
    assert "OAUTH2_PROXY_PROVIDER" not in result.output


def test_services_start_rejects_unknown_explicit_targets(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from phlo.cli.commands.services import start as start_module

    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / "docker-compose.yml").write_text("services:\n  rustfs: {}\n")

    class RustfsFakeDiscovery(FakeDiscovery):
        def discover(self) -> dict[str, ServiceDefinition]:
            return {"rustfs": _service("rustfs")}

        def get_available_profiles(self) -> set[str]:
            return set()

    monkeypatch.setattr(start_module, "ensure_phlo_dir", lambda: phlo_dir)
    monkeypatch.setattr(start_module, "ServiceDiscovery", RustfsFakeDiscovery)
    monkeypatch.setattr(start_module, "get_project_name", lambda: "demo-project")

    result = CliRunner().invoke(start_module.start_cmd, ["--service", "rustfs,typo"])

    assert result.exit_code != 0
    assert "Unknown service name(s): typo" in result.output


def test_services_start_requires_full_dependency_match_for_setup_companions(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from phlo.cli.commands.services import start as start_module

    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / "docker-compose.yml").write_text(
        "services:\n  openmetadata-mysql: {}\n  openmetadata-elasticsearch: {}\n"
        "  openmetadata-setup: {}\n",
    )

    mysql = _service("openmetadata-mysql")
    elasticsearch = _service("openmetadata-elasticsearch")
    setup = _service(
        "openmetadata-setup",
        depends_on=["openmetadata-mysql", "openmetadata-elasticsearch"],
    )

    class OpenMetadataFakeDiscovery(FakeDiscovery):
        def discover(self) -> dict[str, ServiceDefinition]:
            return {
                mysql.name: mysql,
                elasticsearch.name: elasticsearch,
                setup.name: setup,
            }

        def resolve_dependencies(
            self, services: list[ServiceDefinition]
        ) -> list[ServiceDefinition]:
            names = {service.name for service in services}
            ordered = [mysql, elasticsearch, setup]
            return [service for service in ordered if service.name in names]

        def get_available_profiles(self) -> set[str]:
            return set()

    docker_calls: list[list[str]] = []

    def _fake_run_command(cmd: list[str], check=False, capture_output=False) -> CompletedProcess:
        docker_calls.append(cmd)
        return CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(start_module, "ensure_phlo_dir", lambda: phlo_dir)
    monkeypatch.setattr(start_module, "ServiceDiscovery", OpenMetadataFakeDiscovery)
    monkeypatch.setattr(start_module, "get_project_name", lambda: "demo-project")
    monkeypatch.setattr(start_module, "compose_base_cmd", lambda **_kwargs: ["docker", "compose"])
    monkeypatch.setattr(start_module, "run_command", _fake_run_command)
    monkeypatch.setattr(start_module, "require_container_backend", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        start_module, "_emit_service_lifecycle_events", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(start_module, "_run_service_hooks", lambda *args, **kwargs: None)

    # Dependency edges resolve downwards only: starting a dependency must not
    # pull in services that depend on it, so openmetadata-setup stays stopped.
    result = CliRunner().invoke(start_module.start_cmd, ["--service", "openmetadata-mysql"])

    assert result.exit_code == 0
    assert docker_calls
    assert docker_calls[0][-1] == "openmetadata-mysql"
    assert "openmetadata-setup" not in docker_calls[0]


def test_load_native_env_overrides_merges_env_files(tmp_path) -> None:
    from phlo.cli.commands.services import start as start_module

    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / ".env").write_text("PHLO_API_PORT=54000\nOBSERVATORY_PORT=3001\n")
    (phlo_dir / ".env.local").write_text("PHLO_API_PORT=54001\nSECRET_TOKEN='abc123'\n")

    result = start_module._load_native_env_overrides(tmp_path)

    assert result["PHLO_API_PORT"] == "54001"
    assert result["OBSERVATORY_PORT"] == "3001"
    assert result["SECRET_TOKEN"] == "abc123"
