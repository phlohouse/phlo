"""Translation tests for ObserveHookPlugin.

The phlo-observe SDK is optional (absent on Python 3.11), so tests capture
emissions by monkeypatching ``phlo.telemetry.emit`` — the plugin's single
emission surface — and faking the identifier builders. This exercises the
full translation layer without requiring the SDK.
"""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import MagicMock

import pytest

import phlo.telemetry as phlo_observe
from phlo.hooks.events import (
    HookCorrelation,
    IngestionEvent,
    QualityResultEvent,
    RunEvidenceObservationEvent,
    TransformEvent,
)


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture every phlo_observe.emit call as a kwargs dict.

    ``enabled`` is patched live too: _handle gates on it before translating,
    and the real check needs the optional SDK which 3.11 test runs lack.
    """
    calls: list[dict[str, Any]] = []

    def _capture(name: str, **kwargs: Any) -> None:
        calls.append({"name": name, **kwargs})

    monkeypatch.setattr(phlo_observe, "emit", _capture)
    monkeypatch.setattr(phlo_observe, "enabled", lambda: True)
    return calls


@pytest.fixture
def fake_identifiers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide deterministic identifier builders for entity assertions."""

    class _Ids:
        @staticmethod
        def asset_id(*parts: str) -> str:
            return "asset://" + "/".join(str(part) for part in parts)

        @staticmethod
        def table_id(name: str) -> str:
            return f"table://{name}"

        @staticmethod
        def branch_id(system: str, branch: str) -> str:
            return f"branch://{system}/{branch}"

        @staticmethod
        def run_id_for(system: str, run: Any) -> str:
            return f"run://{system}/{run}"

        @staticmethod
        def service_id(name: str) -> str:
            return f"service://{name}"

    import phlo_observe_plugin.hooks_plugin as plugin_mod

    monkeypatch.setattr(plugin_mod, "_identifiers", lambda: _Ids)
    return _Ids


@pytest.fixture
def plugin() -> Any:
    mod = importlib.import_module("phlo_observe_plugin.hooks_plugin")
    return mod.ObserveHookPlugin()


def _ingestion(event_type: str = "ingestion.end", **kwargs: Any) -> IngestionEvent:
    return IngestionEvent(
        event_type=event_type,
        asset_key="raw.users",
        table_name="raw.users",
        group_name="raw",
        status=kwargs.pop("status", "success"),
        **kwargs,
    )


def test_ingestion_end_translates_to_load(
    plugin: Any, captured: list, fake_identifiers: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(phlo_observe, "ambient_run_id", lambda: "dagster-run-1")
    plugin._handle(
        _ingestion(
            branch_name="pipeline-run-logical1",
            metrics={"rows_processed": 10},
            correlation=HookCorrelation(run_id="logical1", trace_id="trace-1", job_name="job-1"),
        )
    )
    assert len(captured) == 1
    evt = captured[0]
    assert evt["name"] == "ingestion.load"
    assert evt["category"] == "data"
    assert evt["outcome"] == "success"
    corr = evt["correlation"]
    # Physical attempt wins run membership; logical id becomes an attribute.
    assert corr["run_id"] == "dagster-run-1"
    assert corr["job_id"] == "job-1"
    assert corr["trace_id"] == "trace-1"
    assert corr["branch"] == "pipeline-run-logical1"
    assert corr["table"] == "raw.users"
    assert evt["attributes"]["phlo_run_id"] == "logical1"
    assert evt["attributes"]["phlo_event_type"] == "ingestion.end"
    assert evt["attributes"]["rows_processed"] == 10
    entities = evt["entities"]
    assert entities["branch"] == "branch://nessie/pipeline-run-logical1"
    assert entities["asset"] == "asset://raw.users"


def test_ingestion_start_is_not_terminal(
    plugin: Any, captured: list, fake_identifiers: Any
) -> None:
    plugin._handle(_ingestion(event_type="ingestion.start", status=None))
    assert captured[0]["name"] == "ingestion.extract"
    # No explicit outcome: the SDK infers success for a clean instantaneous
    # event; the phase distinction lives in phlo_event_type.
    assert captured[0]["outcome"] is None
    assert captured[0]["attributes"]["phlo_event_type"] == "ingestion.start"


def test_failed_ingestion_outcome(plugin: Any, captured: list, fake_identifiers: Any) -> None:
    plugin._handle(_ingestion(status="failed", error="load blew up"))
    assert captured[0]["outcome"] == "failure"
    assert captured[0]["severity"] == "error"


def test_transform_events(plugin: Any, captured: list, fake_identifiers: Any) -> None:
    plugin._handle(
        TransformEvent(
            event_type="transform.end",
            tool="dbt",
            target="dev",
            model_names=["stg_users"],
            status="failure",
            correlation=HookCorrelation(run_id="logical-9", asset_key="silver.users"),
        )
    )
    evt = captured[0]
    assert evt["name"] == "transform.execute"
    assert evt["outcome"] == "failure"
    assert evt["attributes"]["tool"] == "dbt"
    assert evt["attributes"]["model_names"] == ["stg_users"]
    assert evt["entities"]["asset"] == "asset://silver.users"


def test_quality_check_event(
    plugin: Any, captured: list, fake_identifiers: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(phlo_observe, "ambient_run_id", lambda: "phys-2")
    plugin._handle(
        QualityResultEvent(
            event_type="quality.result",
            asset_key="silver.users",
            check_name="not_null",
            passed=False,
            severity="error",
            correlation=HookCorrelation(run_id="logical-3"),
        )
    )
    evt = captured[0]
    assert evt["name"] == "quality.check"
    assert evt["category"] == "quality"
    assert evt["outcome"] == "failure"
    assert evt["correlation"]["run_id"] == "phys-2"
    assert evt["entities"]["asset"] == "asset://silver.users"


def test_wap_aggregate_becomes_wap_validate(
    plugin: Any, captured: list, fake_identifiers: Any
) -> None:
    plugin._handle(
        QualityResultEvent(
            event_type="quality.result",
            asset_key="__pipeline__",
            check_name="wap.aggregate",
            passed=False,
            metadata={
                "decision": "rejected",
                "dagster_run_id": "phys-7",
                "failed_check_ids": ["c1"],
            },
            correlation=HookCorrelation(run_id="logical-4"),
        )
    )
    evt = captured[0]
    assert evt["name"] == "wap.validate"
    assert evt["category"] == "wap"
    assert evt["delivery"] == "critical"
    assert evt["outcome"] == "failure"
    assert evt["correlation"]["run_id"] == "phys-7"
    assert evt["entities"]["run"] == "run://dagster/phys-7"
    # The placeholder asset must not become an entity.
    assert "asset" not in evt["entities"]
    assert evt["attributes"]["decision"] == "rejected"


def test_wap_validate_passed_with_warnings(
    plugin: Any, captured: list, fake_identifiers: Any
) -> None:
    plugin._handle(
        QualityResultEvent(
            event_type="quality.result",
            asset_key="__pipeline__",
            check_name="wap.aggregate",
            passed=True,
            metadata={"decision": "passed_with_warnings", "dagster_run_id": "p1"},
            correlation=HookCorrelation(run_id="logical-5"),
        )
    )
    assert captured[0]["outcome"] == "partial"
    assert captured[0]["severity"] == "warn"


def _observation(**kwargs: Any) -> RunEvidenceObservationEvent:
    return RunEvidenceObservationEvent(
        event_type="run_evidence.observation",
        observation_type="publish",
        correlation=HookCorrelation(run_id="logical-6", project_id="p"),
        **kwargs,
    )


def test_wap_promotion_observation(plugin: Any, captured: list, fake_identifiers: Any) -> None:
    plugin._handle(
        _observation(
            status="success",
            catalog_change={
                "operation": "promotion",
                "catalog_ref": "main",
                "wap_branch": "pipeline-run-logical6",
                "dagster_run_id": "phys-9",
                "merge_outcome": "promoted",
            },
        )
    )
    evt = captured[0]
    assert evt["name"] == "wap.promote"
    assert evt["category"] == "wap"
    assert evt["delivery"] == "critical"
    assert evt["outcome"] == "success"
    # WAP branch wins branch identity; the mutated ref stays an attribute.
    assert evt["correlation"]["branch"] == "pipeline-run-logical6"
    assert evt["correlation"]["run_id"] == "phys-9"
    assert evt["entities"]["branch"] == "branch://nessie/pipeline-run-logical6"
    assert evt["entities"]["run"] == "run://dagster/phys-9"
    assert evt["attributes"]["catalog_ref"] == "main"
    # No catalog_system in the change payload defaults to the Nessie owner.
    assert evt["attributes"]["catalog_system"] == "nessie"


def test_wap_observation_uses_catalog_system(
    plugin: Any, captured: list, fake_identifiers: Any
) -> None:
    """Snapshot-strategy WAP refs are owned by the resolved catalog, so the
    branch entity must name that system rather than hardcoding Nessie."""
    plugin._handle(
        _observation(
            status="success",
            catalog_change={
                "operation": "promotion",
                "catalog_ref": "main",
                "wap_branch": "pipeline-run-logical7",
                "catalog_system": "polaris",
                "dagster_run_id": "phys-9",
                "merge_outcome": "promoted",
            },
        )
    )
    evt = captured[0]
    assert evt["name"] == "wap.promote"
    assert evt["entities"]["branch"] == "branch://polaris/pipeline-run-logical7"
    assert evt["attributes"]["catalog_system"] == "polaris"


def test_ingestion_uses_catalog_system(plugin: Any, captured: list, fake_identifiers: Any) -> None:
    """Ingestion events carry the staging ref's owning catalog so snapshot
    WAP runs do not emit phantom branch://nessie entities."""
    plugin._handle(
        _ingestion(
            branch_name="pipeline-run-logical8",
            catalog_system="polaris",
        )
    )
    evt = captured[0]
    assert evt["entities"]["branch"] == "branch://polaris/pipeline-run-logical8"
    assert evt["attributes"]["catalog_system"] == "polaris"


def test_wap_quality_rejection_maps_to_reject(
    plugin: Any, captured: list, fake_identifiers: Any
) -> None:
    plugin._handle(
        _observation(
            status="rejected",
            catalog_change={
                "operation": "promotion",
                "catalog_ref": "main",
                "wap_branch": "pipeline-run-x",
                "dagster_run_id": "phys-10",
                "merge_outcome": "rejected_quality",
            },
        )
    )
    evt = captured[0]
    assert evt["name"] == "wap.reject"
    assert evt["delivery"] == "critical"
    assert evt["outcome"] == "failure"
    assert evt["severity"] == "error"


def test_wap_promotion_failure_is_promote_failure(
    plugin: Any, captured: list, fake_identifiers: Any
) -> None:
    plugin._handle(
        _observation(
            status="failed",
            catalog_change={
                "operation": "promotion",
                "catalog_ref": "main",
                "dagster_run_id": "phys-11",
                "merge_outcome": "failed",
            },
        )
    )
    evt = captured[0]
    assert evt["name"] == "wap.promote"
    assert evt["outcome"] == "failure"


def test_wap_cleanup_observation(plugin: Any, captured: list, fake_identifiers: Any) -> None:
    plugin._handle(
        _observation(
            status="success",
            catalog_change={
                "operation": "cleanup",
                "catalog_ref": "pipeline-run-y",
                "dagster_run_id": "phys-12",
                "merge_outcome": "deleted",
            },
        )
    )
    evt = captured[0]
    assert evt["name"] == "wap.cleanup"
    assert evt["outcome"] == "success"
    assert evt["correlation"]["branch"] == "pipeline-run-y"


def test_non_catalog_observation_is_generic(
    plugin: Any, captured: list, fake_identifiers: Any
) -> None:
    plugin._handle(_observation(status="success", stage_id="materialize"))
    evt = captured[0]
    assert evt["name"] == "phlo.observation"
    assert evt["attributes"]["stage_id"] == "materialize"


def test_plugin_runs_without_sdk(plugin: Any) -> None:
    """With no SDK, emission no-ops but translation must not raise."""
    plugin._handle(_ingestion())
    plugin._handle(
        _observation(
            status="success",
            catalog_change={"operation": "promotion", "catalog_ref": "main"},
        )
    )


def test_metadata_and_hooks(plugin: Any) -> None:
    assert plugin.metadata.name == "observe"
    hooks = plugin.get_hooks()
    assert len(hooks) == 1
    assert hooks[0].hook_name == "observe_events"


def test_unknown_event_type_ignored(plugin: Any, captured: list) -> None:
    class _Other:
        pass

    plugin._handle(MagicMock())
    assert captured == []
