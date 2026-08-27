"""Tests DbtSettings project-path resolution.

An explicit DBT_PROJECT_DIR wins; otherwise settings fall back to the
discovered nested transforms/dbt project with its profiles directory.
"""

from __future__ import annotations

from phlo_dbt.settings import DbtSettings


def test_dbt_settings_discovers_nested_project_when_default_path_missing(
    monkeypatch, tmp_path
) -> None:
    dbt_project = tmp_path / "workflows" / "client_exports" / "transforms" / "dbt"
    dbt_project.mkdir(parents=True)
    (dbt_project / "dbt_project.yml").write_text("name: client_exports\n")
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.delenv("DBT_PROJECT_DIR", raising=False)

    settings = DbtSettings()

    assert settings.dbt_project_path == dbt_project
    assert settings.dbt_profiles_path == dbt_project / "profiles"


def test_dbt_settings_keeps_explicit_project_dir(monkeypatch, tmp_path) -> None:
    configured = tmp_path / "analytics" / "dbt"
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("DBT_PROJECT_DIR", "analytics/dbt")

    settings = DbtSettings()

    assert settings.dbt_project_path == configured
    assert settings.dbt_profiles_path == configured / "profiles"


def test_dbt_settings_project_dirs_activate_multiple_projects(monkeypatch, tmp_path) -> None:
    sales = tmp_path / "workflows" / "sales" / "transforms" / "dbt"
    finance = tmp_path / "workflows" / "finance" / "transforms" / "dbt"
    for project in (sales, finance):
        project.mkdir(parents=True)
        (project / "dbt_project.yml").write_text("name: x\n")
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "DBT_PROJECT_DIRS", "workflows/sales/transforms/dbt,workflows/finance/transforms/dbt"
    )

    settings = DbtSettings()

    assert settings.dbt_project_paths == [sales, finance]
    assert settings.dbt_project_path == sales


def test_dbt_settings_single_project_paths_when_dirs_unset(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.delenv("DBT_PROJECT_DIRS", raising=False)

    settings = DbtSettings()

    assert settings.dbt_project_paths == [settings.dbt_project_path]


def test_dbt_settings_namespaced_keys_flag(monkeypatch) -> None:
    monkeypatch.setenv("DBT_NAMESPACED_ASSET_KEYS", "1")
    assert DbtSettings().dbt_namespaced_asset_keys is True
    monkeypatch.delenv("DBT_NAMESPACED_ASSET_KEYS")
    assert DbtSettings().dbt_namespaced_asset_keys is False
