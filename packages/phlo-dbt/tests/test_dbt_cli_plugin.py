"""Tests for dbt CLI commands.

Covers dbt run routing: container execution via the orchestrator by
default, host dbt in local mode, actionable errors for missing projects or
missing exec services, and lineage manifest import only after successful
runs (never for compile or failed runs).
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace

from click.testing import CliRunner

import phlo_dbt.settings as dbt_settings
from phlo_dbt.cli_plugin import DbtCliPlugin, dbt_group


def test_dbt_cli_plugin_metadata() -> None:
    plugin = DbtCliPlugin()

    assert plugin.metadata.name == "dbt"
    assert plugin.get_cli_commands()[0].name == "dbt"


def test_dbt_run_uses_active_orchestrator_container_by_default(monkeypatch, tmp_path) -> None:
    project_dir = tmp_path / "workflows" / "transforms" / "dbt"
    profiles_dir = project_dir / "profiles"
    profiles_dir.mkdir(parents=True)
    (project_dir / "dbt_project.yml").write_text("name: demo\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("phlo_dbt.cli_plugin.ensure_phlo_dir", lambda: tmp_path / ".phlo")
    monkeypatch.setattr("phlo_dbt.cli_plugin.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo.orchestrators.get_active_orchestrator",
        lambda: SimpleNamespace(exec_service_name=lambda: "orchestrator"),
    )
    monkeypatch.setattr(
        "phlo_dbt.cli_plugin.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr(
        "phlo_dbt.cli_plugin.get_settings",
        lambda: SimpleNamespace(
            dbt_project_path=project_dir,
            dbt_project_paths=[project_dir],
            dbt_profiles_path=profiles_dir,
            dbt_profiles_path_for=lambda _p: profiles_dir,
        ),
        raising=False,
    )
    monkeypatch.setattr(
        dbt_settings,
        "get_settings",
        lambda: SimpleNamespace(
            dbt_project_path=project_dir,
            dbt_project_paths=[project_dir],
            dbt_profiles_path=profiles_dir,
            dbt_profiles_path_for=lambda _p: profiles_dir,
        ),
    )
    imported_manifests: list[Path] = []
    monkeypatch.setattr(
        "phlo_dbt.cli_plugin._import_manifest_lineage",
        lambda manifest_path: (
            imported_manifests.append(manifest_path) or {"asset_edges": 1, "column_mappings": 0}
        ),
    )

    captured: list[list[str]] = []
    monkeypatch.setattr(
        "phlo_dbt.cli_plugin.subprocess",
        SimpleNamespace(
            run=lambda cmd, check=False, cwd=None: captured.append(cmd) or CompletedProcess(cmd, 0)
        ),
    )

    result = CliRunner().invoke(dbt_group, ["run", "--select", "dim_pokemon"])

    assert result.exit_code == 0
    assert captured == [
        [
            "docker",
            "compose",
            "-p",
            "demo",
            "exec",
            "-T",
            "orchestrator",
            "dbt",
            "run",
            "--project-dir",
            "/app/workflows/transforms/dbt",
            "--profiles-dir",
            "/app/workflows/transforms/dbt/profiles",
            "--target",
            "dev",
            "--select",
            "dim_pokemon",
        ]
    ]
    assert imported_manifests == [project_dir / "target" / "manifest.json"]


def test_dbt_run_local_uses_host_dbt(monkeypatch, tmp_path) -> None:
    project_dir = tmp_path / "workflows" / "transforms" / "dbt"
    profiles_dir = project_dir / "profiles"
    profiles_dir.mkdir(parents=True)
    (project_dir / "dbt_project.yml").write_text("name: demo\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        dbt_settings,
        "get_settings",
        lambda: SimpleNamespace(
            dbt_project_path=project_dir,
            dbt_project_paths=[project_dir],
            dbt_profiles_path=profiles_dir,
            dbt_profiles_path_for=lambda _p: profiles_dir,
        ),
    )
    monkeypatch.setattr("phlo_dbt.cli_plugin.ensure_dbt_profile", lambda *_args, **_kwargs: None)
    imported_manifests: list[Path] = []
    monkeypatch.setattr(
        "phlo_dbt.cli_plugin._import_manifest_lineage",
        lambda manifest_path: (
            imported_manifests.append(manifest_path) or {"asset_edges": 1, "column_mappings": 0}
        ),
    )

    captured: list[tuple[list[str], str | None]] = []
    monkeypatch.setattr(
        "phlo_dbt.cli_plugin.subprocess",
        SimpleNamespace(
            run=lambda cmd, cwd=None, check=False: (
                captured.append((cmd, cwd)) or CompletedProcess(cmd, 0)
            )
        ),
    )

    result = CliRunner().invoke(dbt_group, ["run", "--local", "--select", "dim_pokemon"])

    assert result.exit_code == 0
    assert captured == [
        (
            [
                "dbt",
                "run",
                "--profiles-dir",
                str(profiles_dir),
                "--target",
                "dev",
                "--select",
                "dim_pokemon",
            ],
            str(project_dir),
        )
    ]
    assert imported_manifests == [project_dir / "target" / "manifest.json"]


def test_dbt_run_missing_project_is_actionable(monkeypatch, tmp_path) -> None:
    project_dir = tmp_path / "workflows" / "transforms" / "dbt"
    profiles_dir = project_dir / "profiles"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        dbt_settings,
        "get_settings",
        lambda: SimpleNamespace(
            dbt_project_path=project_dir,
            dbt_project_paths=[project_dir],
            dbt_profiles_path=profiles_dir,
            dbt_profiles_path_for=lambda _p: profiles_dir,
        ),
    )

    result = CliRunner().invoke(dbt_group, ["run", "--local"])

    assert result.exit_code != 0
    assert "Error: no dbt project found" in result.output
    assert f"Missing: {project_dir / 'dbt_project.yml'}" in result.output
    assert "workflows/<name>/transforms/dbt" in result.output
    assert "Run: phlo workflow create --help" in result.output


def test_dbt_run_container_requires_exec_service(monkeypatch, tmp_path) -> None:
    project_dir = tmp_path / "workflows" / "transforms" / "dbt"
    profiles_dir = project_dir / "profiles"
    profiles_dir.mkdir(parents=True)
    (project_dir / "dbt_project.yml").write_text("name: demo\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("phlo_dbt.cli_plugin.ensure_phlo_dir", lambda: tmp_path / ".phlo")
    monkeypatch.setattr(
        "phlo.orchestrators.get_active_orchestrator",
        lambda: SimpleNamespace(exec_service_name=lambda: None),
    )
    monkeypatch.setattr(
        dbt_settings,
        "get_settings",
        lambda: SimpleNamespace(
            dbt_project_path=project_dir,
            dbt_project_paths=[project_dir],
            dbt_profiles_path=profiles_dir,
            dbt_profiles_path_for=lambda _p: profiles_dir,
        ),
    )

    result = CliRunner().invoke(dbt_group, ["run", "--select", "dim_pokemon"])

    assert result.exit_code != 0
    assert "does not expose a container execution service" in result.output


def test_dbt_run_joins_multiple_select_flags(monkeypatch, tmp_path) -> None:
    project_dir = tmp_path / "workflows" / "transforms" / "dbt"
    profiles_dir = project_dir / "profiles"
    profiles_dir.mkdir(parents=True)
    (project_dir / "dbt_project.yml").write_text("name: demo\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("phlo_dbt.cli_plugin.ensure_phlo_dir", lambda: tmp_path / ".phlo")
    monkeypatch.setattr("phlo_dbt.cli_plugin.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo.orchestrators.get_active_orchestrator",
        lambda: SimpleNamespace(exec_service_name=lambda: "orchestrator"),
    )
    monkeypatch.setattr(
        "phlo_dbt.cli_plugin.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr(
        dbt_settings,
        "get_settings",
        lambda: SimpleNamespace(
            dbt_project_path=project_dir,
            dbt_project_paths=[project_dir],
            dbt_profiles_path=profiles_dir,
            dbt_profiles_path_for=lambda _p: profiles_dir,
        ),
    )
    monkeypatch.setattr(
        "phlo_dbt.cli_plugin._import_manifest_lineage",
        lambda _manifest_path: {"asset_edges": 1, "column_mappings": 0},
    )

    captured: list[list[str]] = []
    monkeypatch.setattr(
        "phlo_dbt.cli_plugin.subprocess",
        SimpleNamespace(
            run=lambda cmd, check=False, cwd=None: captured.append(cmd) or CompletedProcess(cmd, 0)
        ),
    )

    result = CliRunner().invoke(
        dbt_group,
        ["run", "--select", "stg_pokemon", "--select", "dim_pokemon"],
    )

    assert result.exit_code == 0
    assert captured[0][-2:] == ["--select", "stg_pokemon dim_pokemon"]


def test_dbt_run_skips_lineage_import_on_failure(monkeypatch, tmp_path) -> None:
    project_dir = tmp_path / "workflows" / "transforms" / "dbt"
    profiles_dir = project_dir / "profiles"
    profiles_dir.mkdir(parents=True)
    (project_dir / "dbt_project.yml").write_text("name: demo\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        dbt_settings,
        "get_settings",
        lambda: SimpleNamespace(
            dbt_project_path=project_dir,
            dbt_project_paths=[project_dir],
            dbt_profiles_path=profiles_dir,
            dbt_profiles_path_for=lambda _p: profiles_dir,
        ),
    )
    monkeypatch.setattr("phlo_dbt.cli_plugin.ensure_dbt_profile", lambda *_args, **_kwargs: None)

    imported_manifests: list[Path] = []
    monkeypatch.setattr(
        "phlo_dbt.cli_plugin._import_manifest_lineage",
        lambda manifest_path: (
            imported_manifests.append(manifest_path) or {"asset_edges": 1, "column_mappings": 0}
        ),
    )
    monkeypatch.setattr(
        "phlo_dbt.cli_plugin.subprocess",
        SimpleNamespace(run=lambda cmd, cwd=None, check=False: CompletedProcess(cmd, 1)),
    )

    result = CliRunner().invoke(dbt_group, ["run", "--local", "--select", "dim_pokemon"])

    assert result.exit_code == 1
    assert imported_manifests == []


def test_dbt_compile_does_not_import_lineage(monkeypatch, tmp_path) -> None:
    project_dir = tmp_path / "workflows" / "transforms" / "dbt"
    profiles_dir = project_dir / "profiles"
    profiles_dir.mkdir(parents=True)
    (project_dir / "dbt_project.yml").write_text("name: demo\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        dbt_settings,
        "get_settings",
        lambda: SimpleNamespace(
            dbt_project_path=project_dir,
            dbt_project_paths=[project_dir],
            dbt_profiles_path=profiles_dir,
            dbt_profiles_path_for=lambda _p: profiles_dir,
        ),
    )
    monkeypatch.setattr("phlo_dbt.cli_plugin.ensure_dbt_profile", lambda *_args, **_kwargs: None)

    imported_manifests: list[Path] = []
    monkeypatch.setattr(
        "phlo_dbt.cli_plugin._import_manifest_lineage",
        lambda manifest_path: (
            imported_manifests.append(manifest_path) or {"asset_edges": 1, "column_mappings": 0}
        ),
    )
    monkeypatch.setattr(
        "phlo_dbt.cli_plugin.subprocess",
        SimpleNamespace(run=lambda cmd, cwd=None, check=False: CompletedProcess(cmd, 0)),
    )

    result = CliRunner().invoke(dbt_group, ["compile", "--local"])

    assert result.exit_code == 0
    assert imported_manifests == []
