"""Tests for Dagster alerting sensor utilities.

send_alert must resolve the alert sink lazily through the plugin system
and pass severities through untouched; the sink itself owns coercion of
missing or invalid values to ERROR.
"""

from __future__ import annotations

import pytest

from phlo_dagster import alerting_sensor


class _AlertSink:
    def __init__(self) -> None:
        self.calls: list[dict[str, object | None]] = []

    def send_alert(self, **kwargs) -> bool:
        self.calls.append(kwargs)
        return True


@pytest.mark.parametrize("severity", [None, "not-valid"], ids=["none", "invalid-string"])
def test_send_alert_forwards_severity_verbatim(monkeypatch, severity) -> None:
    """send_alert is a pass-through; unrecognised severities reach the sink unchanged."""
    sink = _AlertSink()
    monkeypatch.setattr(
        alerting_sensor,
        "_load_alert_sink",
        lambda: sink,
    )

    result = alerting_sensor.send_alert(title="A", message="B", severity=severity)

    assert result is True
    assert sink.calls == [
        {
            "title": "A",
            "message": "B",
            "severity": severity,
            "asset_name": None,
            "run_id": None,
            "error_message": None,
        }
    ]
