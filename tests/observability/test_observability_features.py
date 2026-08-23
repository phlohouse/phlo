"""Tests for observability features.

Covers metrics models and export helpers, alert creation and manager
destination registration, per-destination alert payload rendering (Slack,
PagerDuty, email), and lineage graph serialization.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from phlo_alerting import Alert, AlertSeverity, get_alert_manager
from phlo_alerting.destinations.email import EmailAlertDestination
from phlo_alerting.destinations.pagerduty import PagerDutyAlertDestination
from phlo_alerting.destinations.slack import SlackAlertDestination
from phlo_alerting.manager import AlertDestination
from phlo_lineage import LineageGraph

from phlo.cli.commands.metrics import _export_json, _format_bytes, _parse_period
from phlo.metrics import AssetMetrics, SummaryMetrics


def test_metrics_models_creation() -> None:
    metrics = SummaryMetrics(total_runs_24h=100, successful_runs_24h=95, failed_runs_24h=5)
    assert metrics.total_runs_24h == 100
    asset = AssetMetrics(asset_name="glucose_entries", average_duration=15.5, failure_rate=0.05)
    assert asset.asset_name == "glucose_entries"


def test_alert_creation() -> None:
    alert = Alert(
        title="Test Alert",
        message="This is a test",
        severity=AlertSeverity.WARNING,
        asset_name="test_asset",
        run_id="run_123",
    )
    assert alert.title == "Test Alert"
    assert alert.severity == AlertSeverity.WARNING


def test_alert_manager_registration() -> None:
    manager = get_alert_manager()
    initial_count = len(manager.destinations)

    class MockDestination(AlertDestination):
        def send(self, alert):
            return True

    manager.register_destination("mock", MockDestination())
    assert len(manager.destinations) >= initial_count


def test_alert_payload_building() -> None:
    slack = SlackAlertDestination(webhook_url="https://hooks.slack.com/test")
    slack_payload = slack._build_payload(
        Alert(
            title="Test Alert",
            message="This is a test",
            severity=AlertSeverity.ERROR,
            asset_name="test_asset",
            run_id="run_123",
        )
    )
    assert slack_payload["attachments"][0]["title"] == "Test Alert"

    pagerduty = PagerDutyAlertDestination(integration_key="test_key")
    pagerduty_payload = pagerduty._build_payload(
        Alert(
            title="Critical Alert",
            message="System failure",
            severity=AlertSeverity.CRITICAL,
            asset_name="critical_asset",
        )
    )
    assert pagerduty_payload["routing_key"] == "test_key"

    email = EmailAlertDestination(
        smtp_host="smtp.example.com",
        recipients=["test@example.com"],
    )
    html_content = email._build_html(
        Alert(
            title="Email Test",
            message="Test email content",
            severity=AlertSeverity.WARNING,
            error_message="Something went wrong",
        )
    )
    assert "Email Test" in html_content


def test_lineage_graph_formats() -> None:
    graph = LineageGraph()
    graph.add_asset("raw", asset_type="ingestion")
    graph.add_asset("stage", asset_type="transform")
    graph.add_edge("raw", "stage")
    assert "stage" in graph.to_ascii_tree("raw", direction="downstream")
    assert "digraph" in graph.to_dot()
    assert "graph TD" in graph.to_mermaid()
    data = json.loads(graph.to_json())
    assert "assets" in data
    assert "edges" in data


def test_metrics_export_helpers() -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as handle:
        output_path = Path(handle.name)
    try:
        _export_json(SummaryMetrics(total_runs_24h=100), output_path)
        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert data["total_runs_24h"] == 100
        assert "exported_at" in data
    finally:
        output_path.unlink()

    assert _format_bytes(1024) == "1.00 KB"
    assert _parse_period("7d") == 168


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
