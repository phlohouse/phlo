"""Tests nested dbt project discovery.

A dbt_project.yml below workflows/<source>/transforms/dbt must be found both
by direct search and by get_dbt_project_dir when DBT_PROJECT_DIR is unset.
"""

from __future__ import annotations

from pathlib import Path

from phlo_dbt.discovery import find_dbt_projects, get_dbt_project_dir


def test_find_dbt_projects_discovers_nested_transforms_project(tmp_path: Path) -> None:
    dbt_project = tmp_path / "workflows" / "client_exports" / "transforms" / "dbt"
    dbt_project.mkdir(parents=True)
    (dbt_project / "dbt_project.yml").write_text("name: client_exports\n")

    assert find_dbt_projects(tmp_path) == [dbt_project]


def test_get_dbt_project_dir_discovers_nested_transforms_project(
    tmp_path: Path, monkeypatch
) -> None:
    dbt_project = tmp_path / "workflows" / "client_exports" / "transforms" / "dbt"
    dbt_project.mkdir(parents=True)
    (dbt_project / "dbt_project.yml").write_text("name: client_exports\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DBT_PROJECT_DIR", raising=False)

    assert get_dbt_project_dir() == dbt_project
