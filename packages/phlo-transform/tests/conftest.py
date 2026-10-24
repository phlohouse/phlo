"""Fixtures shared by the phlo-transform provider tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from phlo.plugins.observatory_settings import _reset_memory_service
from phlo_transform import editor
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


@pytest.fixture
def editable(tmp_path, monkeypatch):
    monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_BACKEND", "memory")
    _reset_memory_service()
    root = tmp_path / "transforms"
    root.mkdir()
    (root / "phlo.toml").write_text('[transform]\ndefault_catalog="iceberg"\n')
    (root / "transforms").mkdir()
    (root / "transforms" / "inspection.sql").write_text("SELECT * FROM gold.batch_release_metrics")
    model = WorkspaceModel(
        id="model://review/inspection",
        name="review.inspection",
        namespace="review",
        materialization="table",
        target="iceberg.review.inspection",
        version="abcdef0",
        path="transforms/inspection.sql",
        tags=(),
        depends_on=(),
        sources=("gold.batch_release_metrics",),
    )
    workspace = TransformWorkspace(
        root=root,
        binary=tmp_path / "engine",
        models=(model,),
        tests=(),
        sources=("gold.batch_release_metrics",),
    )
    settings = TransformSettings(
        PHLO_TRANSFORM_BIN=str(workspace.binary),
        PHLO_TRANSFORM_ADAPTER="trino",
        PHLO_TRANSFORM_TRINO_ENDPOINT="http://trino:8080",
        PHLO_TRANSFORM_TRINO_CATALOG="iceberg",
        PHLO_TRANSFORM_TRINO_PASSWORD="synthetic-test-password",
    )
    monkeypatch.setattr(editor, "provider_is_active", lambda root: True)
    monkeypatch.setattr(editor, "load_workspace", lambda root, settings: workspace)
    monkeypatch.setattr(editor, "get_settings", lambda root: settings)
    monkeypatch.setattr(editor, "require_binary", lambda root, settings: workspace.binary)
    yield tmp_path, workspace
    _reset_memory_service()
