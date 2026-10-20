"""Fixtures shared by the phlo-transform provider tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from phlo_transform.settings import TransformSettings
from phlo_transform.workspace import TransformWorkspace, WorkspaceModel, WorkspaceTest


@pytest.fixture
def list_payload() -> dict:
    """Return a two-model ``phlo-transform list --json`` report."""

    return {
        "models": [
            {
                "id": "model://analytics/stg_orders",
                "name": "analytics.stg_orders",
                "namespace": "analytics",
                "materialization": "view",
                "target": "analytics.stg_orders",
                "version": "abc1234",
                "path": "transforms/stg_orders.sql",
                "tags": ["staging"],
                "depends_on": [],
                "sources": ["raw.orders"],
            },
            {
                "id": "model://analytics/orders",
                "name": "analytics.orders",
                "namespace": "analytics",
                "materialization": "table",
                "target": "analytics.orders",
                "version": "def5678",
                "path": "transforms/orders.sql",
                "depends_on": ["analytics.stg_orders"],
                "sources": [],
            },
        ],
        "sources": [{"id": "source://raw/orders", "name": "raw.orders"}],
        "tests": [
            {
                "id": "test://analytics/orders/not_null_id",
                "name": "analytics.orders.generated.not_null_id",
                "targets": ["analytics.orders"],
            }
        ],
        "seeds": [],
    }


@pytest.fixture
def transform_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TransformSettings:
    """Return provider settings scoped to a temporary project."""

    monkeypatch.setenv("PHLO_TRANSFORM_BIN", str(tmp_path / "phlo-transform"))
    monkeypatch.setenv("PHLO_TRANSFORM_ADAPTER", "duckdb")
    monkeypatch.delenv("PHLO_TRANSFORMATION_PROVIDER", raising=False)
    return TransformSettings(_project_root=tmp_path)


@pytest.fixture
def fake_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TransformWorkspace:
    """Return a two-model workspace matching ``list_payload``."""

    root = tmp_path / "workflows" / "transforms" / "native"
    root.mkdir(parents=True)
    (root / "phlo.toml").write_text('[transform]\ndefault_namespace = "analytics"\n')
    models = [
        WorkspaceModel(
            id="model://analytics/stg_orders",
            name="analytics.stg_orders",
            namespace="analytics",
            materialization="view",
            target="analytics.stg_orders",
            version="abc1234",
            path="transforms/stg_orders.sql",
            tags=("staging",),
            depends_on=(),
            sources=("raw.orders",),
        ),
        WorkspaceModel(
            id="model://analytics/orders",
            name="analytics.orders",
            namespace="analytics",
            materialization="table",
            target="analytics.orders",
            version="def5678",
            path="transforms/orders.sql",
            tags=(),
            depends_on=("analytics.stg_orders",),
            sources=(),
        ),
    ]
    tests = [
        WorkspaceTest(
            id="test://analytics/orders/not_null_id",
            name="analytics.orders.generated.not_null_id",
            targets=("analytics.orders",),
        )
    ]
    workspace = TransformWorkspace(
        root=root,
        binary=tmp_path / "phlo-transform",
        models=tuple(models),
        tests=tuple(tests),
        sources=("raw.orders",),
    )
    monkeypatch.setattr("phlo_transform.assets.load_workspace", lambda *args, **kwargs: workspace)
    return workspace
