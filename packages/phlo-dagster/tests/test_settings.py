"""Verify DagsterSettings resolves workflows_path across the PHLO_WORKFLOWS_PATH
and legacy WORKFLOWS_PATH aliases."""

from __future__ import annotations

from phlo_dagster.settings import DagsterSettings


def test_dagster_settings_accepts_phlo_workflows_path_alias(monkeypatch) -> None:
    monkeypatch.setenv("PHLO_WORKFLOWS_PATH", "/app/workflows")
    monkeypatch.delenv("WORKFLOWS_PATH", raising=False)

    assert DagsterSettings().workflows_path == "/app/workflows"


def test_dagster_settings_accepts_legacy_workflows_path_alias(monkeypatch) -> None:
    monkeypatch.delenv("PHLO_WORKFLOWS_PATH", raising=False)
    monkeypatch.setenv("WORKFLOWS_PATH", "/legacy/workflows")

    assert DagsterSettings().workflows_path == "/legacy/workflows"
