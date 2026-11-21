"""Tests for "phlo services add" install selection after removal and re-enable.

Removing then re-adding a service must clear its disabled state so the
service is selected for install again on the next compose regeneration.
"""

from __future__ import annotations

import subprocess

import pytest
import yaml
from click.testing import CliRunner

from phlo.cli.infrastructure.selection import select_services_to_install
from phlo.plugins.discovery import ServiceDefinition
from tests.support import FakeDiscovery, _service


def test_services_add_clears_disabled_after_remove_and_reenables_install_selection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Verify remove+add clears disabled state and re-selects the service for install."""
    postgres = _service("postgres", default=True)
    prometheus = _service("prometheus")
    services = {postgres.name: postgres, prometheus.name: prometheus}
    selected_names: list[str] = []

    class AddRemoveFakeDiscovery(FakeDiscovery):
        def discover(self) -> dict[str, ServiceDefinition]:
            return services

        def get_default_services(self, disabled_services=None) -> list[ServiceDefinition]:
            return [postgres]

    def fake_regenerate_compose(discovery, config: dict, _phlo_dir) -> None:
        selected = select_services_to_install(
            all_services=discovery.discover(),
            default_services=discovery.get_default_services(),
            enabled_names=config.get("services", {}).get("enabled", []),
            disabled_names=config.get("services", {}).get("disabled", []),
        )
        selected_names[:] = [service.name for service in selected]

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".phlo").mkdir()
    config_file = tmp_path / "phlo.yaml"
    config_file.write_text(
        yaml.dump(
            {
                "services": {
                    "enabled": ["prometheus"],
                    "disabled": [],
                }
            },
            sort_keys=False,
        )
    )

    from phlo.cli.commands.services import add as add_module
    from phlo.cli.commands.services import remove as remove_module

    monkeypatch.setattr(add_module, "ServiceDiscovery", AddRemoveFakeDiscovery)
    monkeypatch.setattr(remove_module, "ServiceDiscovery", AddRemoveFakeDiscovery)
    monkeypatch.setattr(remove_module, "_regenerate_compose", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(add_module, "_regenerate_compose", fake_regenerate_compose)

    runner = CliRunner()
    remove_result = runner.invoke(remove_module.remove_cmd, ["prometheus", "--keep-running"])
    assert remove_result.exit_code == 0

    after_remove = yaml.safe_load(config_file.read_text())
    assert after_remove["services"]["enabled"] == []
    assert after_remove["services"]["disabled"] == ["prometheus"]

    add_result = runner.invoke(add_module.add_cmd, ["prometheus", "--no-start"])
    assert add_result.exit_code == 0

    after_add = yaml.safe_load(config_file.read_text())
    assert after_add["services"]["enabled"] == ["prometheus"]
    assert after_add["services"]["disabled"] == []
    assert "prometheus" in selected_names


def test_services_add_normalizes_enabled_disabled_lists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Verify add command normalizes configured enabled/disabled service lists."""
    postgres = _service("postgres", default=True)
    minio = _service("minio", default=True)
    prometheus = _service("prometheus")
    services = {svc.name: svc for svc in [postgres, minio, prometheus]}

    class NormalizeFakeDiscovery(FakeDiscovery):
        def discover(self) -> dict[str, ServiceDefinition]:
            return services

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".phlo").mkdir()
    config_file = tmp_path / "phlo.yaml"
    config_file.write_text(
        yaml.dump(
            {
                "services": {
                    "enabled": [" prometheus ", "PROMETHEUS", "minio", "", 42],
                    "disabled": [" prometheus ", " POSTGRES ", None],
                }
            },
            sort_keys=False,
        )
    )

    from phlo.cli.commands.services import add as add_module

    monkeypatch.setattr(add_module, "ServiceDiscovery", NormalizeFakeDiscovery)
    monkeypatch.setattr(add_module, "_regenerate_compose", lambda *_args, **_kwargs: None)

    runner = CliRunner()
    add_result = runner.invoke(add_module.add_cmd, ["prometheus", "--no-start"])
    assert add_result.exit_code == 0

    after_add = yaml.safe_load(config_file.read_text())
    assert after_add["services"]["enabled"] == ["minio", "prometheus"]
    assert after_add["services"]["disabled"] == ["postgres"]


def test_services_add_remove_report_malformed_phlo_yaml_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".phlo").mkdir()
    (tmp_path / "phlo.yaml").write_text("name: [unterminated\n")

    from phlo.cli.commands.services import add as add_module
    from phlo.cli.commands.services import remove as remove_module

    runner = CliRunner()
    for command, args in (
        (add_module.add_cmd, ["prometheus", "--no-start"]),
        (remove_module.remove_cmd, ["prometheus", "--keep-running"]),
    ):
        result = runner.invoke(command, args)

        assert result.exit_code == 1
        assert "invalid phlo.yaml" in result.output
        assert "Traceback" not in result.output
        assert not isinstance(result.exception, yaml.YAMLError)


def test_services_add_installs_the_package_of_a_registry_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A service the registry lists is installed, then added, in one command."""
    from phlo.cli.commands.services import add as add_module
    from phlo.plugins.registry_client import RegistryPlugin

    grafana = _service("grafana")
    services: dict[str, ServiceDefinition] = {}
    installed: list[list[str]] = []

    class InstallingFakeDiscovery(FakeDiscovery):
        def discover(self) -> dict[str, ServiceDefinition]:
            return dict(services)

    def fake_install(packages: list[str]) -> str:
        installed.append(packages)
        services["grafana"] = grafana
        return "project"

    entry = RegistryPlugin(
        name="grafana",
        type="service",
        package="phlo-grafana",
        version=None,
        description="Dashboards",
        author="Phlo Team",
        homepage=None,
        tags=[],
        verified=True,
        core=False,
    )
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".phlo").mkdir()
    (tmp_path / "phlo.yaml").write_text("services:\n  enabled: []\n")
    monkeypatch.setattr(add_module, "ServiceDiscovery", InstallingFakeDiscovery)
    monkeypatch.setattr(add_module, "_regenerate_compose", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("phlo.plugins.registry_client.get_plugin", lambda name: entry)
    monkeypatch.setattr("phlo.plugins.project_install.install_packages", fake_install)

    result = CliRunner().invoke(add_module.add_cmd, ["grafana", "--no-start"])

    assert result.exit_code == 0, result.output
    assert installed == [["phlo-grafana"]]
    assert yaml.safe_load((tmp_path / "phlo.yaml").read_text())["services"]["enabled"] == [
        "grafana"
    ]


def test_services_add_reports_a_package_it_cannot_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from phlo.cli.commands.services import add as add_module
    from phlo.plugins.project_install import PluginInstallError
    from phlo.plugins.registry_client import RegistryPlugin

    class EmptyDiscovery(FakeDiscovery):
        def discover(self) -> dict[str, ServiceDefinition]:
            return {}

    def refuse(packages: list[str]) -> str:
        raise PluginInstallError("phlo-grafana is not published with phlo 0.19.0")

    entry = RegistryPlugin(
        name="grafana",
        type="service",
        package="phlo-grafana",
        version=None,
        description="Dashboards",
        author="Phlo Team",
        homepage=None,
        tags=[],
        verified=True,
        core=False,
    )
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".phlo").mkdir()
    (tmp_path / "phlo.yaml").write_text("services:\n  enabled: []\n")
    monkeypatch.setattr(add_module, "ServiceDiscovery", EmptyDiscovery)
    monkeypatch.setattr("phlo.plugins.registry_client.get_plugin", lambda name: entry)
    monkeypatch.setattr("phlo.plugins.project_install.install_packages", refuse)

    result = CliRunner().invoke(add_module.add_cmd, ["grafana", "--no-start"])

    assert result.exit_code == 1
    assert "phlo-grafana is not published with phlo 0.19.0" in result.output
    assert "Traceback" not in result.output


def test_adding_the_observer_points_dagster_at_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Dagster containers send events to the in-stack observer, not the host's localhost."""
    from phlo.cli.commands.services import add as add_module
    from phlo.cli.main import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OBSERVE_HTTP_ENDPOINT", raising=False)
    (tmp_path / "phlo.yaml").write_text("name: lake\n")
    init = CliRunner().invoke(cli, ["services", "init", "--no-dev", "--force"])
    assert init.exit_code == 0, init.output

    result = CliRunner().invoke(add_module.add_cmd, ["phlo-observer", "--no-start"])

    assert result.exit_code == 0, result.output
    compose = yaml.safe_load((tmp_path / ".phlo" / "docker-compose.yml").read_text())
    for name in ("dagster", "dagster-daemon"):
        environment = compose["services"][name]["environment"]
        assert environment["OBSERVE_HTTP_ENDPOINT"] == "http://phlo-observer:8080/v1/events"


def test_services_add_restarts_dagster_after_installing_a_package(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Dagster installs project packages on start, so it is restarted to load the new one."""
    from phlo.cli.commands.services import add as add_module
    from phlo.plugins.registry_client import RegistryPlugin

    kafka = _service("kafka")
    services: dict[str, ServiceDefinition] = {}
    commands: list[list[str]] = []

    class InstallingFakeDiscovery(FakeDiscovery):
        def discover(self) -> dict[str, ServiceDefinition]:
            return dict(services)

    def fake_install(packages: list[str]) -> str:
        services["kafka"] = kafka
        return "project"

    def fake_compose(cmd, **_kwargs):
        commands.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    entry = RegistryPlugin(
        name="kafka",
        type="service",
        package="phlo-kafka",
        version=None,
        description="Kafka",
        author="Phlo Team",
        homepage=None,
        tags=[],
        verified=True,
        core=False,
    )
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".phlo").mkdir()
    (tmp_path / ".phlo" / "docker-compose.yml").write_text(
        "services:\n  dagster: {}\n  dagster-code: {}\n  kafka: {}\n"
    )
    (tmp_path / "phlo.yaml").write_text("services:\n  enabled: []\n")
    monkeypatch.setattr(add_module, "ServiceDiscovery", InstallingFakeDiscovery)
    monkeypatch.setattr(add_module, "_regenerate_compose", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(add_module, "compose_base_cmd", lambda **_kwargs: ["compose"])
    monkeypatch.setattr(add_module, "run_compose", fake_compose)
    monkeypatch.setattr(add_module, "get_project_name", lambda: "lake")
    monkeypatch.setattr("phlo.plugins.registry_client.get_plugin", lambda name: entry)
    monkeypatch.setattr("phlo.plugins.project_install.install_packages", fake_install)

    result = CliRunner().invoke(add_module.add_cmd, ["kafka"])

    assert result.exit_code == 0, result.output
    assert commands == [
        ["compose", "up", "-d", "kafka"],
        ["compose", "restart", "dagster-code", "dagster"],
    ]
