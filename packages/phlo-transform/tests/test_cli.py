"""CLI forwarding, provider switching, and status output."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from phlo_transform import cli_plugin
from phlo_transform.binary import Invocation


class _Registry:
    def list(self, family: str) -> list[str]:
        return ["dbt", "transform"] if family == "transformation_provider" else []


def _invocation(stdout: str = "", returncode: int = 0) -> Invocation:
    return Invocation(
        command=("phlo-transform",),
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_workspace):
    """Run CLI tests in a project root with a resolvable workspace."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PHLO_TRANSFORMATION_PROVIDER", raising=False)
    monkeypatch.setattr(cli_plugin, "_require_workspace", lambda settings: fake_workspace)
    return tmp_path


def test_check_forwards_json_flag(monkeypatch: pytest.MonkeyPatch, cli_env: Path) -> None:
    calls: list[tuple[list[str], list[str] | None]] = []

    def fake_run_cli(binary, args, *, settings, root, extra_args=None):
        calls.append((list(args), extra_args))
        return _invocation('{"ok": true}\n')

    monkeypatch.setattr(cli_plugin, "run_cli", fake_run_cli)

    result = CliRunner().invoke(cli_plugin.transform_group, ["check", "--json"])

    assert result.exit_code == 0
    assert calls == [(["check"], ["--json"])]
    assert result.output.strip() == '{"ok": true}'


def test_run_forwards_selection_and_exit_code(
    monkeypatch: pytest.MonkeyPatch, cli_env: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run_cli(binary, args, *, settings, root, extra_args=None):
        calls.append(list(args))
        return _invocation("run failed", returncode=3)

    monkeypatch.setattr(cli_plugin, "run_cli", fake_run_cli)

    result = CliRunner().invoke(
        cli_plugin.transform_group,
        ["run", "--select", "analytics.orders", "--force", "--adapter", "duckdb"],
    )

    assert result.exit_code == 3
    assert calls == [["run", "--select", "analytics.orders", "--force", "--adapter", "duckdb"]]


def test_use_writes_project_marker(monkeypatch: pytest.MonkeyPatch, cli_env: Path) -> None:
    monkeypatch.setattr("phlo.plugins.discovery.discover_plugins", lambda **kwargs: None)
    monkeypatch.setattr("phlo.plugins.discovery.get_global_registry", lambda: _Registry())

    result = CliRunner().invoke(cli_plugin.transform_group, ["use", "phlo-transform"])

    assert result.exit_code == 0, result.output
    marker = cli_env / ".phlo" / "transformation-provider"
    assert marker.read_text().strip() == "transform"


def test_use_rejects_uninstalled_provider(monkeypatch: pytest.MonkeyPatch, cli_env: Path) -> None:
    monkeypatch.setattr("phlo.plugins.discovery.discover_plugins", lambda **kwargs: None)
    monkeypatch.setattr("phlo.plugins.discovery.get_global_registry", lambda: _Registry())

    result = CliRunner().invoke(cli_plugin.transform_group, ["use", "spark"])

    assert result.exit_code != 0
    assert "is not installed" in result.output


def test_status_reports_active_provider_and_counts(
    monkeypatch: pytest.MonkeyPatch, cli_env: Path
) -> None:
    binary = cli_env / "phlo-transform"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setattr(cli_plugin, "find_binary", lambda root, settings: binary)
    monkeypatch.setattr(
        cli_plugin, "binary_version", lambda binary, settings: "phlo-transform 0.1.0"
    )
    from phlo_transform.workspace import TransformWorkspace

    workspace = TransformWorkspace(
        root=cli_env / "workspace",
        binary=binary,
        models=(),
        tests=(),
        sources=(),
    )
    monkeypatch.setattr(
        cli_plugin, "load_workspace", lambda root, settings, require=True: workspace
    )

    result = CliRunner().invoke(cli_plugin.transform_group, ["status", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["workspace"].endswith("workspace")
    assert payload["binary_version"] == "phlo-transform 0.1.0"
    assert payload["models"] == 0


def test_translate_check_forwards_to_the_engine(
    monkeypatch: pytest.MonkeyPatch, cli_env: Path
) -> None:
    calls: list[dict] = []

    def fake_translate(*args, **kwargs):
        calls.append(kwargs)
        return _invocation("dbt migration analysis\n")

    monkeypatch.setattr(cli_plugin, "translate_dbt_project", fake_translate)

    result = CliRunner().invoke(cli_plugin.transform_group, ["translate", "--check"])

    assert result.exit_code == 0, result.output
    assert calls[0]["check_only"] is True
    assert "dbt migration analysis" in result.output


def test_install_reports_the_installed_binary(
    monkeypatch: pytest.MonkeyPatch, cli_env: Path
) -> None:
    binary = cli_env / "phlo-transform"
    monkeypatch.setattr(cli_plugin, "install_binary", lambda *args, **kwargs: binary)
    monkeypatch.setattr(
        cli_plugin, "binary_version", lambda *args, **kwargs: "phlo-transform 0.1.0"
    )

    result = CliRunner().invoke(cli_plugin.transform_group, ["install"])

    assert result.exit_code == 0, result.output
    assert "Installed" in result.output
    assert "phlo-transform 0.1.0" in result.output


def test_use_unset_removes_the_marker(cli_env: Path) -> None:
    marker = cli_env / ".phlo" / "transformation-provider"
    marker.parent.mkdir()
    marker.write_text("transform\n")

    result = CliRunner().invoke(cli_plugin.transform_group, ["use", "--unset"])

    assert result.exit_code == 0, result.output
    assert not marker.exists()
