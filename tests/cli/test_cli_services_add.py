"""Tests for "phlo services add" install selection after removal and re-enable.

Removing then re-adding a service must clear its disabled state so the
service is selected for install again on the next compose regeneration.
"""

from __future__ import annotations

import pytest
import yaml
from click.testing import CliRunner

from phlo.cli.infrastructure.selection import select_services_to_install
from phlo.plugins.discovery import ServiceDefinition
from tests.helpers import FakeDiscovery, _service


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
