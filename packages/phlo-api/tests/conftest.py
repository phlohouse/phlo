"""Test-module import path for API-local helpers.

Also forces every test onto the non-durable memory observatory settings store
and resets it around each test.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import pytest

TEST_DIR = str(Path(__file__).parent)
if TEST_DIR not in sys.path:
    sys.path.insert(0, TEST_DIR)


@pytest.fixture(autouse=True)
def use_memory_observatory_settings_store(monkeypatch) -> None:
    """API unit tests explicitly use the non-durable development backend."""
    monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_BACKEND", "memory")
    from phlo.plugins.observatory_settings import _reset_memory_service

    _reset_memory_service()


_OBSERVATORY_LOADER_SEAMS: tuple[str, ...] = (
    "asset_detail",
    "asset_graph",
    "asset_impact",
    "asset_neighbors",
    "assets",
    "branch_detail",
    "branches",
    "capability_registry",
    "capability_registry_uncached",
    "capabilities",
    "dataset_profile",
    "dataset_usage",
    "dataset_workflow_state",
    "datasets",
    "extension_detail",
    "extensions",
    "governance_matrix",
    "lakehouse_manifest",
    "log_facets",
    "logs",
    "operation_detail",
    "operations",
    "pipelines",
    "project_log_events",
    "provider_branches",
    "publishing_readiness",
    "quality",
    "quality_detail",
    "row_journey",
    "runs",
    "saved_queries",
    "service_detail",
    "services",
    "settings",
    "stage_diff",
    "surface_capabilities",
    "table_preview",
    "tables",
    "tables_without_catalog",
    "wap_report_operations",
)


def _as_loader(value: object) -> Callable[..., object]:
    """Turn a fixture-supplied value into a drop-in observatory loader."""
    if callable(value):
        return value
    if isinstance(value, list):
        return lambda *_args, **_kwargs: list(value)
    return lambda *_args, **_kwargs: value


@pytest.fixture
def observatory_loaders(monkeypatch: pytest.MonkeyPatch) -> Callable[..., dict[str, object]]:
    """Patch observatory read-model loaders at a single seam.

    Usage::

        observatory_loaders(assets=[asset], quality=[], logs=[], operations=[])

    Keyword names mirror the ``_load_*`` functions on
    ``phlo_api.observatory_api.observatory`` minus the ``_load_`` prefix.
    Every known seam is validated up front, so a loader rename or removal
    fails here instead of in individual tests. List constants return a fresh
    ``list`` copy per call, other constants are returned as-is, and callables
    are installed verbatim (handy for loaders that record invocations).
    Loaders left unsupplied keep their real implementation.
    """
    from phlo_api.observatory_api import observatory

    missing = [
        name for name in _OBSERVATORY_LOADER_SEAMS if not hasattr(observatory, f"_load_{name}")
    ]
    if missing:
        raise AssertionError(f"observatory loaders renamed or removed: {missing}")
    patched: dict[str, object] = {}

    def factory(**loaders: object) -> dict[str, object]:
        for key, value in loaders.items():
            if key not in _OBSERVATORY_LOADER_SEAMS:
                raise AssertionError(f"unknown observatory loader seam: {key}")
            monkeypatch.setattr(observatory, f"_load_{key}", _as_loader(value))
            patched[key] = value
        return dict(patched)

    return factory
