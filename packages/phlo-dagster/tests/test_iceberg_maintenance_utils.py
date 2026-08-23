"""Tests for Iceberg maintenance telemetry helpers.

Verifies that start/complete maintenance operations emit telemetry events
whose correlation carries the Dagster run id and job name taken from the run
context, with completion evidence attached to the payload.
"""

from __future__ import annotations

from types import SimpleNamespace

from phlo.hooks.events import TelemetryEvent
from phlo_dagster.iceberg_maintenance_utils import (
    MaintenanceConfig,
    finish_maintenance_op,
    start_maintenance_op,
)


def test_maintenance_emitters_propagate_context_correlation(monkeypatch) -> None:
    class RecordingBus:
        def __init__(self) -> None:
            self.events: list[object] = []

        def emit(self, event: object) -> None:
            self.events.append(event)

    bus = RecordingBus()
    monkeypatch.setattr("phlo.hooks.emitters.get_hook_bus", lambda: bus)

    log = SimpleNamespace(info=lambda *args, **kwargs: None)
    context = SimpleNamespace(run_id="run-91", job_name="maintenance_job", log=log)
    config = MaintenanceConfig()

    telemetry = start_maintenance_op(context, config, "expire_snapshots")
    finish_maintenance_op(
        context,
        config,
        telemetry,
        "expire_snapshots",
        duration_seconds=3.5,
        errors=[],
        tables_processed=4,
        evidence={"results": [{"table_name": "raw.events", "status": "succeeded"}]},
    )

    telemetry_events = [event for event in bus.events if isinstance(event, TelemetryEvent)]
    assert telemetry_events
    for event in telemetry_events:
        assert event.correlation.run_id == "run-91"
        assert event.correlation.job_name == "maintenance_job"

    complete = [event for event in telemetry_events if event.name == "iceberg.maintenance.complete"]
    assert complete[0].payload["evidence"]["results"][0]["status"] == "succeeded"
