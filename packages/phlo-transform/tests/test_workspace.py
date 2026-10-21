"""Workspace discovery and report parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from phlo.exceptions import PhloCapabilitySetupError
from phlo_transform import workspace as workspace_module
from phlo_transform.binary import Invocation
from phlo_transform.settings import TransformSettings
from phlo_transform.workspace import (
    discover_workspaces,
    find_workspace,
    load_workspace,
)


def _invocation(returncode: int = 0, stdout: str = "", stderr: str = "") -> Invocation:
    return Invocation(
        command=("phlo-transform",), returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_discovery_prefers_canonical_root(tmp_path: Path) -> None:
    canonical = tmp_path / "workflows" / "transforms"
    canonical.mkdir(parents=True)
    (canonical / "phlo.toml").write_text("[transform]\n")
    nested = canonical / "native"
    nested.mkdir()
    (nested / "phlo.toml").write_text("[transform]\n")

    assert discover_workspaces(tmp_path) == [canonical]
    assert find_workspace(tmp_path, TransformSettings(_project_root=tmp_path)) == canonical


def test_multiple_workspaces_require_explicit_root(tmp_path: Path) -> None:
    for name in ("a", "b"):
        root = tmp_path / "workflows" / name / "transforms"
        root.mkdir(parents=True)
        (root / "phlo.toml").write_text("[transform]\n")

    with pytest.raises(PhloCapabilitySetupError, match="multiple phlo-transform workspaces"):
        find_workspace(tmp_path, TransformSettings(_project_root=tmp_path))


def test_explicit_root_must_contain_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "not-a-workspace").mkdir()
    monkeypatch.setenv("PHLO_TRANSFORM_ROOT", "not-a-workspace")
    with pytest.raises(PhloCapabilitySetupError, match="does not contain phlo.toml"):
        find_workspace(tmp_path, TransformSettings(_project_root=tmp_path))


def test_load_workspace_parses_models_tests_and_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, list_payload: dict
) -> None:
    root = tmp_path / "workflows" / "transforms"
    root.mkdir(parents=True)
    (root / "phlo.toml").write_text("[transform]\n")
    binary = tmp_path / "phlo-transform"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setattr(workspace_module, "require_binary", lambda *args, **kwargs: binary)
    monkeypatch.setattr(
        workspace_module, "run_json", lambda *args, **kwargs: (list_payload, _invocation())
    )

    loaded = load_workspace(tmp_path, TransformSettings(_project_root=tmp_path))

    assert loaded is not None
    assert [model.name for model in loaded.models] == ["analytics.stg_orders", "analytics.orders"]
    assert loaded.models[1].depends_on == ("analytics.stg_orders",)
    assert loaded.models[0].leaf_name == "stg_orders"
    assert loaded.tests_for("analytics.orders")[0].name == (
        "analytics.orders.generated.not_null_id"
    )
    assert loaded.sources == ("raw.orders",)


def test_source_asset_bindings_are_read_from_the_workspace(tmp_path: Path) -> None:
    from phlo_transform.workspace import load_source_assets

    root = tmp_path / "workspace"
    root.mkdir()
    (root / "phlo.toml").write_text(
        '[transform]\ndefault_namespace = "main"\n\n'
        '[phlo_transform.sources]\n"raw.orders" = "sling_orders"\n'
    )

    assert load_source_assets(root) == {"raw.orders": "sling_orders"}


def test_load_workspace_without_binary_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workflows" / "transforms"
    root.mkdir(parents=True)
    (root / "phlo.toml").write_text("[transform]\n")
    monkeypatch.setattr(workspace_module, "find_binary", lambda *args, **kwargs: None)

    assert (
        load_workspace(tmp_path, TransformSettings(_project_root=tmp_path), require=False) is None
    )


def test_list_failure_reports_engine_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "workflows" / "transforms"
    root.mkdir(parents=True)
    (root / "phlo.toml").write_text("[transform]\n")
    binary = tmp_path / "phlo-transform"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setattr(workspace_module, "require_binary", lambda *args, **kwargs: binary)
    monkeypatch.setattr(
        workspace_module,
        "run_json",
        lambda *args, **kwargs: (None, _invocation(returncode=2, stderr="WORKSPACE_ERROR: bad")),
    )

    with pytest.raises(PhloCapabilitySetupError, match="could not list the workspace"):
        load_workspace(tmp_path, TransformSettings(_project_root=tmp_path))
