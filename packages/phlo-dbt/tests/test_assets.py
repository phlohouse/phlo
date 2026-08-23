"""Tests error handling in build_dbt_asset_specs.

A missing dbt project yields an empty spec list; an unavailable or
structurally invalid manifest raises PhloCapabilitySetupError with a
diagnostic reason instead of returning partial assets.
"""

from __future__ import annotations

import json

import pytest

from phlo.exceptions import PhloCapabilitySetupError
from phlo_dbt.assets import build_dbt_asset_specs


def test_build_dbt_asset_specs_returns_empty_when_project_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "phlo_dbt.assets.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "dbt_project_path": tmp_path / "missing",
                "dbt_profiles_path": tmp_path / "missing" / "profiles",
            },
        )(),
    )

    assert build_dbt_asset_specs() == []


def test_build_dbt_asset_specs_raises_when_manifest_unavailable(monkeypatch, tmp_path) -> None:
    project_path = tmp_path / "dbt"
    profiles_path = project_path / "profiles"
    project_path.mkdir(parents=True)
    (project_path / "dbt_project.yml").write_text("name: test\nversion: '1.0'\n", encoding="utf-8")

    monkeypatch.setattr(
        "phlo_dbt.assets.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "dbt_project_path": project_path,
                "dbt_profiles_path": profiles_path,
            },
        )(),
    )
    monkeypatch.setattr("phlo_dbt.assets.ensure_dbt_profile", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("phlo_dbt.assets.ensure_dbt_manifest", lambda *_args, **_kwargs: False)

    with pytest.raises(PhloCapabilitySetupError, match="manifest_unavailable"):
        build_dbt_asset_specs()


def test_build_dbt_asset_specs_raises_when_manifest_shape_is_invalid(monkeypatch, tmp_path) -> None:
    project_path = tmp_path / "dbt"
    profiles_path = project_path / "profiles"
    target_path = project_path / "target"
    project_path.mkdir(parents=True)
    target_path.mkdir(parents=True)
    (project_path / "dbt_project.yml").write_text("name: test\nversion: '1.0'\n", encoding="utf-8")
    (target_path / "manifest.json").write_text(json.dumps({"nodes": []}), encoding="utf-8")

    monkeypatch.setattr(
        "phlo_dbt.assets.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "dbt_project_path": project_path,
                "dbt_profiles_path": profiles_path,
            },
        )(),
    )
    monkeypatch.setattr("phlo_dbt.assets.ensure_dbt_profile", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("phlo_dbt.assets.ensure_dbt_manifest", lambda *_args, **_kwargs: True)

    with pytest.raises(PhloCapabilitySetupError, match="manifest_shape_invalid"):
        build_dbt_asset_specs()
