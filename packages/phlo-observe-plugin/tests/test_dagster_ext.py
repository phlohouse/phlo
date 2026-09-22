"""Tests for the observe run-status sensor (terminal pipeline.run events)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import phlo.telemetry as phlo_observe


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _capture(name: str, **kwargs: Any) -> None:
        calls.append({"name": name, **kwargs})

    monkeypatch.setattr(phlo_observe, "emit", _capture)
    monkeypatch.setattr(phlo_observe, "enabled", lambda: True)
    monkeypatch.setattr(phlo_observe, "run_entity_for", lambda p, r: f"run://{p}/{r}")
    monkeypatch.setattr(
        phlo_observe,
        "branch_entity_id",
        lambda b, *, system="nessie": f"branch://{system}/{b}",
    )
    return calls


def _context(
    *,
    run_id: str = "phys-1",
    job_name: str = "job-a",
    status: str = "SUCCESS",
    tags: dict[str, str] | None = None,
    end_ts: float = 1700000060.0,
    start_ts: float = 1700000000.0,
) -> Any:
    """Mirror the real RunStatusSensorContext surface: ``dagster_event`` is a
    DagsterEvent with no timestamp; run timing comes from get_run_stats."""
    run = SimpleNamespace(
        run_id=run_id,
        job_name=job_name,
        tags=tags or {},
        root_run_id="root-1",
        parent_run_id=None,
        asset_selection=frozenset({"a", "b"}),
    )
    instance = MagicMock()
    instance.get_run_stats.return_value = SimpleNamespace(
        start_time=start_ts, launch_time=start_ts, end_time=end_ts
    )
    return SimpleNamespace(
        dagster_run=run,
        dagster_event=SimpleNamespace(),
        instance=instance,
    )


def test_success_run_emits_terminal_pipeline_run(captured: list) -> None:
    from phlo_observe_plugin.dagster_ext import _emit_pipeline_run

    _emit_pipeline_run(_context(tags={"phlo/run_id": "logical-1"}), "SUCCESS")
    assert len(captured) == 1
    evt = captured[0]
    assert evt["name"] == "pipeline.run"
    assert evt["category"] == "pipeline"
    assert evt["outcome"] == "success"
    assert evt["severity"] == "info"
    corr = evt["correlation"]
    assert corr["run_id"] == "phys-1"
    assert corr["root_run_id"] == "root-1"
    assert corr["job_id"] == "job-a"
    assert evt["attributes"]["phlo_run_id"] == "logical-1"
    assert evt["producer"] == "dagster"
    assert evt["entities"]["run"] == "run://dagster/phys-1"
    assert evt["duration_ms"] == pytest.approx(60000.0)
    assert evt["started_at"] is not None and evt["ended_at"] is not None


def test_failure_run_maps_outcome_and_severity(captured: list) -> None:
    from phlo_observe_plugin.dagster_ext import _emit_pipeline_run

    _emit_pipeline_run(_context(), "FAILURE")
    assert captured[0]["outcome"] == "failure"
    assert captured[0]["severity"] == "error"


def test_dagster_run_status_enum_maps_to_outcome(captured: list) -> None:
    """The real caller passes a DagsterRunStatus enum, not a bare string."""
    dg = pytest.importorskip("dagster")
    from phlo_observe_plugin.dagster_ext import _emit_pipeline_run

    _emit_pipeline_run(_context(), dg.DagsterRunStatus.FAILURE)
    assert captured[0]["outcome"] == "failure"
    assert captured[0]["severity"] == "error"
    assert captured[0]["attributes"]["dagster_status"] == "FAILURE"


def test_canceled_maps_to_cancelled(captured: list) -> None:
    from phlo_observe_plugin.dagster_ext import _emit_pipeline_run

    _emit_pipeline_run(_context(), "CANCELED")
    assert captured[0]["outcome"] == "cancelled"


def test_wap_branch_tag_links_branch_entity(captured: list) -> None:
    from phlo_observe_plugin.dagster_ext import _emit_pipeline_run

    _emit_pipeline_run(_context(tags={"phlo/wap_branch": "pipeline-run-9"}), "SUCCESS")
    evt = captured[0]
    assert evt["correlation"]["branch"] == "pipeline-run-9"
    assert evt["entities"]["branch"] == "branch://nessie/pipeline-run-9"


def test_wap_catalog_system_tag_names_branch_owner(captured: list) -> None:
    """Snapshot-strategy runs tag the ref's owning catalog; the branch entity
    must follow it rather than defaulting to nessie."""
    from phlo_observe_plugin.dagster_ext import _emit_pipeline_run

    _emit_pipeline_run(
        _context(
            tags={
                "phlo/wap_branch": "pipeline-run-9",
                "phlo/catalog_system": "polaris",
            }
        ),
        "SUCCESS",
    )
    evt = captured[0]
    assert evt["entities"]["branch"] == "branch://polaris/pipeline-run-9"


def test_disabled_sdk_skips_emission(captured: list, monkeypatch: pytest.MonkeyPatch) -> None:
    """enabled() False (SDK absent or observability off) skips the stats query
    and the emission entirely."""
    monkeypatch.setattr(phlo_observe, "enabled", lambda: False)
    from phlo_observe_plugin.dagster_ext import _emit_pipeline_run

    _emit_pipeline_run(_context(), "SUCCESS")
    assert captured == []


class _RaisingRun:
    def __getattr__(self, name: str) -> Any:
        raise RuntimeError("broken context")


def test_context_failure_is_contained(captured: list) -> None:
    from phlo_observe_plugin.dagster_ext import _emit_pipeline_run

    # A context whose every attribute raises must not propagate.
    _emit_pipeline_run(MagicMock(dagster_run=_RaisingRun()), "SUCCESS")


def test_extension_registers_sensors() -> None:
    pytest.importorskip("dagster")
    from phlo_observe_plugin.dagster_ext import ObserveDagsterExtension

    defs = ObserveDagsterExtension().get_definitions()
    sensor_names = {s.name for s in defs.sensors}
    assert sensor_names == {
        "observe_run_success",
        "observe_run_failure",
        "observe_run_canceled",
    }


def test_sensors_default_to_running() -> None:
    """run_status_sensor defaults to STOPPED; these sensors must auto-start.

    If they registered STOPPED the daemon would never evaluate them and no
    terminal ``pipeline.run`` event would ever be emitted.
    """
    dg = pytest.importorskip("dagster")
    from phlo_observe_plugin.dagster_ext import ObserveDagsterExtension

    defs = ObserveDagsterExtension().get_definitions()
    for sensor in defs.sensors:
        assert sensor.default_status == dg.DefaultSensorStatus.RUNNING
