"""Tests for Pandera decorator helper emitters.

Quality-result and telemetry emitters must propagate run correlation (run id,
partition key, asset key) onto the shared hook bus.
"""

from __future__ import annotations

from types import SimpleNamespace

from phlo.hooks.events import QualityResultEvent, TelemetryEvent
from phlo_pandera.decorator_helpers import _make_emitters


def test_make_emitters_propagates_runtime_correlation(monkeypatch) -> None:
    class RecordingBus:
        def __init__(self) -> None:
            self.events: list[object] = []

        def emit(self, event: object) -> None:
            self.events.append(event)

    bus = RecordingBus()
    monkeypatch.setattr("phlo.hooks.emitters.get_hook_bus", lambda: bus)

    runtime = SimpleNamespace(run_id="run-51", job_name="quality_job")
    emitter, telemetry = _make_emitters(
        runtime,
        "silver.orders",
        "2026-03-08",
        "pandera",
        "trino",
    )

    emitter.emit_result(check_name="not_null_order_id", passed=True)
    telemetry.emit_metric(name="quality.rows_validated", value=10, unit="rows")

    quality_event = next(event for event in bus.events if isinstance(event, QualityResultEvent))
    telemetry_event = next(event for event in bus.events if isinstance(event, TelemetryEvent))

    assert quality_event.correlation.run_id == "run-51"
    assert quality_event.correlation.partition_key == "2026-03-08"
    assert quality_event.correlation.asset_key == "silver.orders"
    assert telemetry_event.correlation.run_id == "run-51"
    assert telemetry_event.correlation.partition_key == "2026-03-08"
    assert telemetry_event.correlation.asset_key == "silver.orders"
