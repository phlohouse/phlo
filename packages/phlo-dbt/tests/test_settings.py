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
