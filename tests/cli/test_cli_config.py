"""Tests for the "phlo config" CLI group.

Covers show in YAML and JSON, validate behavior for missing or defaulted
infrastructure, schema-error and malformed-YAML reporting without tracebacks,
and upgrade writing defaults while respecting --force.
"""

from __future__ import annotations

import json

import yaml
from click.testing import CliRunner

from phlo.cli.config import config as config_group
from phlo.config_schema import InfrastructureConfig, ServiceConfig


def test_config_show_supports_yaml_and_json(monkeypatch) -> None:
    infra = InfrastructureConfig(
        services={"dagster": ServiceConfig(service_name="dagster-webserver")}
    )
    monkeypatch.setattr("phlo.cli.config.load_infrastructure_config", lambda: infra)

    runner = CliRunner()

    yaml_result = runner.invoke(config_group, ["show"])
    assert yaml_result.exit_code == 0
    assert "Effective Infrastructure Configuration" in yaml_result.output
    assert "dagster-webserver" in yaml_result.output

    json_result = runner.invoke(config_group, ["show", "--format", "json"])
    assert json_result.exit_code == 0
    assert '"dagster-webserver"' in json_result.output


def test_config_validate_handles_missing_and_defaulted_infrastructure(
    tmp_path, monkeypatch
) -> None:
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    missing_result = runner.invoke(config_group, ["validate"])
    assert missing_result.exit_code == 1
    assert "No phlo.yaml found" in missing_result.output

    (tmp_path / "phlo.yaml").write_text("name: demo\n")
    default_result = runner.invoke(config_group, ["validate"])
    assert default_result.exit_code == 0
    assert "No infrastructure section in phlo.yaml" in default_result.output


def test_config_validate_reports_schema_errors(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "phlo.yaml").write_text(
        yaml.safe_dump(
            {
                "infrastructure": {
                    "container_naming_pattern": "invalid",
                }
            }
        )
    )

    result = CliRunner().invoke(config_group, ["validate"])

    assert result.exit_code == 1
    assert "Validation Error" in result.output
    assert "container_naming_pattern" in result.output


def test_config_commands_report_malformed_yaml_without_traceback(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "phlo.yaml").write_text("name: [unterminated\n")

    runner = CliRunner()

    for command in (["show"], ["validate"], ["upgrade"]):
        result = runner.invoke(config_group, command)

        assert result.exit_code == 1
        assert "invalid phlo.yaml" in result.output
        assert "Traceback" not in result.output
        assert not isinstance(result.exception, yaml.YAMLError)


def test_config_upgrade_writes_defaults_and_respects_force(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "phlo.yaml"
    config_path.write_text("name: demo\n")
    cleared: list[bool] = []
    monkeypatch.setattr("phlo.cli.config.clear_config_cache", lambda: cleared.append(True))

    runner = CliRunner()

    upgrade_result = runner.invoke(config_group, ["upgrade"])
    assert upgrade_result.exit_code == 0
    upgraded = yaml.safe_load(config_path.read_text())
    assert "infrastructure" in upgraded
    assert cleared == [True]

    skip_result = runner.invoke(config_group, ["upgrade"])
    assert skip_result.exit_code == 1
    assert "Infrastructure section already exists" in skip_result.output

    force_result = runner.invoke(config_group, ["upgrade", "--force"])
    assert force_result.exit_code == 0


def test_config_json_validation_reports_defaults_and_failures(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    missing = runner.invoke(config_group, ["validate", "--json"])
    assert missing.exit_code == 1
    assert json.loads(missing.stdout)["errors"]
    (tmp_path / "phlo.yaml").write_text("name: demo\n")
    valid = runner.invoke(config_group, ["validate", "--json"])
    assert valid.exit_code == 0, valid.output
    payload = json.loads(valid.stdout)
    assert payload["data"]["valid"] is True
    assert payload["warnings"]
    (tmp_path / "phlo.yaml").write_text("name: [unterminated\n")
    invalid = runner.invoke(config_group, ["validate", "--json"])
    assert invalid.exit_code == 1
    assert json.loads(invalid.stdout)["errors"]


def test_config_show_json_envelope_preserves_raw_format(monkeypatch):
    monkeypatch.setattr("phlo.cli.config.load_infrastructure_config", InfrastructureConfig)
    runner = CliRunner()
    structured = json.loads(runner.invoke(config_group, ["show", "--json"]).stdout)
    raw = json.loads(runner.invoke(config_group, ["show", "--format", "json"]).stdout)
    assert structured["data"] == raw
