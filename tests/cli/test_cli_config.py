"""Tests for the "phlo config" CLI group.

Covers show in YAML and JSON, validate behavior for missing or defaulted
infrastructure, schema-error and malformed-YAML reporting without tracebacks,
and upgrade writing defaults while respecting --force.
"""

from __future__ import annotations

import json

import pytest
import yaml
from click.testing import CliRunner

from phlo.cli.config import config as config_group
from phlo.cli.main import cli
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


def test_config_upgrade_plan_only_detects_and_plans_without_applying(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "phlo.yaml"
    config_path.write_text("name: demo\n")

    runner = CliRunner()
    result = runner.invoke(config_group, ["upgrade", "--plan-only"])

    assert result.exit_code == 0
    assert "infrastructure: add" in result.output
    assert "no changes applied" in result.output
    assert "infrastructure" not in yaml.safe_load(config_path.read_text())


def test_config_upgrade_plan_only_prints_skip_for_existing_infrastructure(
    tmp_path, monkeypatch
) -> None:
    """--plan-only must still emit the plan when a step is a no-op skip."""
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "phlo.yaml"
    config_path.write_text("name: demo\ninfrastructure:\n  platform: local\n")

    runner = CliRunner()
    result = runner.invoke(config_group, ["upgrade", "--plan-only"])

    assert result.exit_code == 0
    assert "infrastructure: skip" in result.output
    assert "no changes applied" in result.output


def test_config_upgrade_validate_rejects_uncoercible_infrastructure(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "phlo.yaml"
    original = "name: demo\ninfrastructure: {}\n"
    config_path.write_text(original)
    monkeypatch.setattr(
        "phlo.cli.config.InfrastructureConfig",
        type(
            "BadInfra",
            (),
            {
                "model_dump": lambda self, **kwargs: {"injected": True},
                "model_validate": staticmethod(
                    lambda value: (_ for _ in ()).throw(ValueError("bad infra"))
                ),
            },
        ),
    )

    runner = CliRunner()
    result = runner.invoke(config_group, ["upgrade", "--force"])

    assert result.exit_code == 1
    assert "validation failed" in result.output
    # A rejected candidate must not replace the installed configuration.
    assert config_path.read_text() == original


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


def test_public_config_preserves_canonical_overrides_and_metadata(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    services = {
        "dagster": {
            "enabled": False,
            "ports": ["8080:3000"],
            "environment": {"TOKEN": "${TOKEN}"},
            "files": {"dagster/dagster.yaml": {"source": "instance.yaml"}},
        },
        "grafana": {
            "service_name": "metrics",
            "container_name": "custom-metrics",
            "host": "metrics.example.com",
            "internal_host": "metrics-internal",
            "files": {
                "grafana/dashboards/infrastructure.json": {
                    "source": "dashboard.json",
                    "mode": "replace",
                }
            },
        },
    }
    (tmp_path / "phlo.yaml").write_text(
        yaml.safe_dump({"infrastructure": {"services": services}}), encoding="utf-8"
    )
    runner = CliRunner()
    valid = runner.invoke(cli, ["config", "validate", "--json"])
    assert valid.exit_code == 0, valid.output
    assert json.loads(valid.stdout)["data"]["valid"] is True
    shown = runner.invoke(cli, ["config", "show", "--json"])
    assert shown.exit_code == 0, shown.output
    effective = json.loads(shown.stdout)["data"]["infrastructure"]["services"]
    assert effective["dagster"]["service_name"] == "dagster"
    assert effective["dagster"]["files"] == {
        "dagster/dagster.yaml": {"source": "instance.yaml", "mode": "merge"}
    }
    for name, values in services.items():
        for key, value in values.items():
            if key != "files":
                assert effective[name][key] == value
    assert effective["grafana"]["files"] == services["grafana"]["files"]
    assert yaml.safe_load((tmp_path / "phlo.yaml").read_text(encoding="utf-8")) == {
        "infrastructure": {"services": services}
    }


@pytest.mark.parametrize("metadata", [{}, {"service_name": "dagster-webserver"}])
@pytest.mark.parametrize(
    "record",
    [
        {"source": "instance.yaml", "mode": "not-a-mode"},
        {"source": ""},
        {"mode": "replace"},
        {"source": "instance.yaml", "unexpected": True},
    ],
)
def test_public_config_validate_rejects_invalid_canonical_files(
    tmp_path, monkeypatch, metadata, record
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "phlo.yaml").write_text(
        yaml.safe_dump(
            {
                "infrastructure": {
                    "services": {"dagster": {**metadata, "files": {"dagster/dagster.yaml": record}}}
                }
            }
        ),
        encoding="utf-8",
    )
    result = CliRunner().invoke(cli, ["config", "validate", "--json"])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["errors"]
    assert "files" in result.stdout
