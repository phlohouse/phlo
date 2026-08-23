"""Tests for Dagster alerting sensor utilities.

send_alert must resolve the alert sink lazily through the plugin system
and pass severities through untouched; the sink itself owns coercion of
missing or invalid values to ERROR.
"""

from __future__ import annotations

from phlo_dagster import alerting_sensor


class _AlertSink:
    def __init__(self) -> None:
        self.calls: list[dict[str, object | None]] = []

    def send_alert(self, **kwargs) -> bool:
        self.calls.append(kwargs)
        return True


def test_send_alert_coerces_none_severity_to_error(monkeypatch) -> None:
    """Non-string severities should safely default to ERROR."""
    sink = _AlertSink()
    monkeypatch.setattr(
        alerting_sensor,
        "_load_alert_sink",
        lambda: sink,
    )

    result = alerting_sensor.send_alert(title="A", message="B", severity=None)

    assert result is True
    assert sink.calls == [
        {
            "title": "A",
            "message": "B",
            "severity": None,
            "asset_name": None,
            "run_id": None,
            "error_message": None,
        }
    ]


def test_send_alert_coerces_invalid_string_to_error(monkeypatch) -> None:
    """Invalid string severities should safely default to ERROR."""
    sink = _AlertSink()
    monkeypatch.setattr(
        alerting_sensor,
        "_load_alert_sink",
        lambda: sink,
    )

    result = alerting_sensor.send_alert(title="A", message="B", severity="not-valid")

    assert result is True
    assert sink.calls == [
        {
            "title": "A",
            "message": "B",
            "severity": "not-valid",
            "asset_name": None,
            "run_id": None,
            "error_message": None,
        }
    ]
