"""Tests for enabled/disabled service state transitions across lifecycle commands.

Drives remove, add, list, and start in one flow to prove the enabled/disabled
config and the generated compose file stay mutually consistent at every step.
"""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest
import yaml
from click.testing import CliRunner

from phlo.cli.infrastructure.selection import select_services_to_install
from tests.helpers import FakeDiscovery, _service


def _write_project_config(config_file: Path, *, enabled: list[str], disabled: list[str]) -> None:
    config_file.write_text(
        yaml.dump(
            {
                "services": {
                    "enabled": enabled,
                    "disabled": disabled,
                }
            },
            sort_keys=False,
        )
    )


def _load_project_config(config_file: Path) -> dict:
    return yaml.safe_load(config_file.read_text())


def _write_compose_file(phlo_dir: Path, service_names: list[str]) -> None:
    compose = {"services": {name: {} for name in sorted(service_names)}}
    (phlo_dir / "docker-compose.yml").write_text(yaml.dump(compose, sort_keys=False))


def _compose_service_names(phlo_dir: Path) -> list[str]:
    compose = yaml.safe_load((phlo_dir / "docker-compose.yml").read_text()) or {}
    return sorted((compose.get("services") or {}).keys())


def test_services_state_transition_remove_add_list_start_flow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Verify remove/add/list/start preserves consistent service state through full flow."""
    postgres = _service("postgres", default=True)
    prometheus = _service("prometheus", profile="observability")
    services = {
        postgres.name: postgres,
        prometheus.name: prometheus,
    }
    fake_discovery = FakeDiscovery(services, default_names=(postgres.name,))

    def _fake_regenerate_compose(discovery, config: dict, phlo_dir: Path) -> None:
        selected = select_services_to_install(
            all_services=discovery.discover(),
            default_services=discovery.get_default_services(),
            enabled_names=config.get("services", {}).get("enabled", []),
            disabled_names=config.get("services", {}).get("disabled", []),
        )
        _write_compose_file(phlo_dir, [service.name for service in selected])

    monkeypatch.chdir(tmp_path)
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    _write_compose_file(phlo_dir, [postgres.name, prometheus.name])

    config_file = tmp_path / "phlo.yaml"
    _write_project_config(config_file, enabled=[prometheus.name], disabled=[])

    from phlo.cli.commands.services import add as add_module
    from phlo.cli.commands.services import list as list_module
    from phlo.cli.commands.services import remove as remove_module
    from phlo.cli.commands.services import start as start_module

    monkeypatch.setattr(add_module, "ServiceDiscovery", lambda: fake_discovery)
    monkeypatch.setattr(remove_module, "ServiceDiscovery", lambda: fake_discovery)
    monkeypatch.setattr(list_module, "ServiceDiscovery", lambda: fake_discovery)
    monkeypatch.setattr(start_module, "ServiceDiscovery", lambda: fake_discovery)

    monkeypatch.setattr(add_module, "_regenerate_compose", _fake_regenerate_compose)
    monkeypatch.setattr(remove_module, "_regenerate_compose", _fake_regenerate_compose)

    monkeypatch.setattr(list_module, "get_project_name", lambda: "demo-project")
    monkeypatch.setattr(list_module, "_get_running_containers", lambda *_args: {})

    docker_calls: list[list[str]] = []

    def _fake_run_command(cmd: list[str], check=False, capture_output=False) -> CompletedProcess:
        docker_calls.append(cmd)
        return CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(start_module, "ensure_phlo_dir", lambda: phlo_dir)
    monkeypatch.setattr(start_module, "get_project_name", lambda: "demo-project")
    monkeypatch.setattr(start_module, "get_profile_service_names", lambda _profiles: ["prometheus"])
    monkeypatch.setattr(start_module, "compose_base_cmd", lambda **_kwargs: ["docker", "compose"])
    monkeypatch.setattr(start_module, "require_container_backend", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        start_module,
        "select_project_container_backend",
        lambda **_kwargs: type(
            "LegacyBackend", (), {"list_project_containers": lambda *_args: []}
        )(),
    )
    monkeypatch.setattr(start_module, "run_command", _fake_run_command)
    monkeypatch.setattr(
        start_module, "_emit_service_lifecycle_events", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(start_module, "_run_service_hooks", lambda *args, **kwargs: None)

    runner = CliRunner()

    remove_result = runner.invoke(remove_module.remove_cmd, [prometheus.name, "--keep-running"])
    assert remove_result.exit_code == 0
    after_remove = _load_project_config(config_file)
    assert after_remove["services"]["enabled"] == []
    assert after_remove["services"]["disabled"] == [prometheus.name]
    assert _compose_service_names(phlo_dir) == [postgres.name]

    add_result = runner.invoke(add_module.add_cmd, [prometheus.name, "--no-start"])
    assert add_result.exit_code == 0
    after_add = _load_project_config(config_file)
    assert after_add["services"]["enabled"] == [prometheus.name]
    assert after_add["services"]["disabled"] == []
    assert _compose_service_names(phlo_dir) == [postgres.name, prometheus.name]

    list_result = runner.invoke(list_module.list_cmd, ["--json"])
    assert list_result.exit_code == 0
    listed_services = {item["name"]: item for item in json.loads(list_result.output)}
    assert listed_services[prometheus.name]["profile"] == "observability"
    assert listed_services[prometheus.name]["disabled"] is False
    assert listed_services[prometheus.name]["running"] is False

    start_result = runner.invoke(start_module.start_cmd, ["--profile", "observability"])
    assert start_result.exit_code == 0
    assert docker_calls
    assert "prometheus" in docker_calls[0]
    assert "postgres" not in docker_calls[0]


def test_services_list_reads_disabled_service_names_from_transition_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Verify list --json marks disabled services from remove/add state arrays."""
    postgres = _service("postgres", default=True)
    prometheus = _service("prometheus", profile="observability")
    services = {
        postgres.name: postgres,
        prometheus.name: prometheus,
    }
    fake_discovery = FakeDiscovery(services, default_names=(postgres.name,))

    monkeypatch.chdir(tmp_path)
    _write_project_config(tmp_path / "phlo.yaml", enabled=[], disabled=[prometheus.name])

    from phlo.cli.commands.services import list as list_module

    monkeypatch.setattr(list_module, "ServiceDiscovery", lambda: fake_discovery)
    monkeypatch.setattr(list_module, "get_project_name", lambda: "demo-project")
    monkeypatch.setattr(list_module, "_get_running_containers", lambda *_args: {})

    result = CliRunner().invoke(list_module.list_cmd, ["--json"])

    assert result.exit_code == 0
    listed_services = {item["name"]: item for item in json.loads(result.output)}
    assert listed_services[prometheus.name]["disabled"] is True
    assert listed_services[postgres.name]["disabled"] is False


def test_services_start_profile_rejects_disabled_only_targets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Verify start --profile fails when profile services are all disabled."""
    monkeypatch.chdir(tmp_path)
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    _write_compose_file(phlo_dir, ["postgres"])
    _write_project_config(tmp_path / "phlo.yaml", enabled=[], disabled=["prometheus"])

    from phlo.cli.commands.services import start as start_module

    class ProfilesOnlyFakeDiscovery(FakeDiscovery):
        def get_available_profiles(self) -> set[str]:
            return {"observability"}

    docker_calls: list[list[str]] = []
    docker_checks: list[bool] = []

    def _record_run_command(cmd: list[str], check=False, capture_output=False) -> CompletedProcess:
        docker_calls.append(cmd)
        return CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(start_module, "ensure_phlo_dir", lambda: phlo_dir)
    monkeypatch.setattr(start_module, "ServiceDiscovery", ProfilesOnlyFakeDiscovery)
    monkeypatch.setattr(start_module, "get_project_name", lambda: "demo-project")
    monkeypatch.setattr(start_module, "get_profile_service_names", lambda _profiles: ["prometheus"])
    monkeypatch.setattr(
        start_module,
        "require_container_backend",
        lambda *_args, **_kwargs: docker_checks.append(True),
    )
    monkeypatch.setattr(start_module, "run_command", _record_run_command)

    result = CliRunner().invoke(start_module.start_cmd, ["--profile", "observability"])

    assert result.exit_code != 0
    assert "profile(s) resolve to no services: observability" in result.output
    assert docker_calls == []
    assert docker_checks == []
