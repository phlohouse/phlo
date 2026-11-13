"""Unit tests for phlo-alerting.

Covers alert construction and severity mapping plus AlertManager delivery,
deduplication, targeted destination routing against in-memory stubs, the
global manager accessor, and the package-root export surface.
"""

from datetime import datetime, timezone

import pytest

from phlo.hooks.events import HookCorrelation, QualityResultEvent

import phlo_alerting
from phlo_alerting import get_alert_manager
from phlo_alerting.manager import Alert, AlertManager, AlertDestination, AlertSeverity
from phlo_alerting.hooks_plugin import (
    AlertingHookPlugin,
    _map_quality_severity,
    _map_telemetry_severity,
)
from phlo_alerting.settings import AlertingSettings


class MockDestination(AlertDestination):
    """In-memory destination stub that records sent alerts."""

    def __init__(self):
        """Initialize the destination with an empty alert buffer."""
        self.alerts: list[Alert] = []

    def send(self, alert: Alert) -> bool:
        """Record an alert and report successful delivery."""
        self.alerts.append(alert)
        return True


def test_alert_defaults():
    """Verifies default alert severity and timestamp assignment."""
    alert = Alert(title="Test", message="hello")
    assert alert.severity == AlertSeverity.ERROR
    assert alert.timestamp.tzinfo is timezone.utc
    assert abs((datetime.now(timezone.utc) - alert.timestamp).total_seconds()) < 60


def test_alert_severity_values():
    """Verifies enum values for alert severity constants."""
    assert AlertSeverity.INFO.value == "info"
    assert AlertSeverity.WARNING.value == "warning"
    assert AlertSeverity.ERROR.value == "error"
    assert AlertSeverity.CRITICAL.value == "critical"


def test_alert_manager_register_and_send():
    """Verifies destination registration and alert delivery path."""
    manager = AlertManager()
    dest = MockDestination()
    manager.register_destination("mock", dest)

    alert = Alert(title="T", message="M", asset_name="a1", error_message="e1")
    assert manager.send(alert) is True
    assert len(dest.alerts) == 1


def test_alert_manager_deduplication():
    """Verifies duplicate alerts are de-duplicated by the manager."""
    manager = AlertManager()
    dest = MockDestination()
    manager.register_destination("mock", dest)

    alert = Alert(title="T", message="M", asset_name="a1", error_message="e1")
    manager.send(alert)
    assert manager.send(alert) is False
    assert len(dest.alerts) == 1


def test_alert_manager_targeted_destinations():
    """Verifies alert routing to only explicitly targeted destinations."""
    manager = AlertManager()
    d1 = MockDestination()
    d2 = MockDestination()
    manager.register_destination("d1", d1)
    manager.register_destination("d2", d2)

    alert = Alert(title="T", message="M")
    manager.send(alert, destinations=["d1"])
    assert len(d1.alerts) == 1
    assert len(d2.alerts) == 0


@pytest.mark.parametrize("first_api", ["send", "send_confirmed"])
@pytest.mark.parametrize("second_api", ["send", "send_confirmed"])
def test_delivery_retries_only_failed_routes_across_send_apis(first_api, second_api):
    manager = AlertManager()
    delivered = MockDestination()

    class RetryingDestination(MockDestination):
        def send(self, alert):
            self.alerts.append(alert)
            return len(self.alerts) > 1

    retrying = RetryingDestination()
    manager.register_destination("delivered", delivered)
    manager.register_destination("retrying", retrying)
    alert = Alert(title="T", message="M", run_id="run-1")
    targets = ["delivered", "retrying"]
    assert getattr(manager, first_api)(alert, targets) is (first_api == "send")
    assert getattr(manager, second_api)(alert, targets)
    assert manager.send_confirmed(alert, targets)
    assert not manager.send(alert, targets)
    assert len(delivered.alerts) == 1
    assert len(retrying.alerts) == 2


def test_replacing_destination_resets_only_its_receipts():
    manager = AlertManager()
    old, replacement, other = MockDestination(), MockDestination(), MockDestination()
    manager.register_destination("target", old)
    manager.register_destination("other", other)
    alert = Alert(title="T", message="M")
    assert manager.send(alert)
    assert manager.delivery_count == 2
    manager.register_destination("target", old)
    assert not manager.send(alert)
    manager.register_destination("target", replacement)
    assert manager.send_confirmed(alert, ["target", "other"])
    assert old.alerts == replacement.alerts == other.alerts == [alert]
    assert manager.delivery_count == 2


def test_alert_identity_separates_checks_runs_and_ambiguous_fields():
    manager = AlertManager()
    destination = MockDestination()
    manager.register_destination("test", destination)
    alerts = [
        Alert(title="nulls", message="failed", run_id="one", asset_name="a:b", error_message="c"),
        Alert(title="nulls", message="failed", run_id="one", asset_name="a", error_message="b:c"),
        Alert(title="unique", message="failed", run_id="one", asset_name="a:b", error_message="c"),
        Alert(title="nulls", message="failed", run_id="two", asset_name="a:b", error_message="c"),
    ]
    assert all(manager.send(alert) for alert in alerts)
    assert not manager.send(alerts[0])
    assert destination.alerts == alerts


def test_slack_receipts_include_effective_channel(monkeypatch):
    from phlo_alerting.destinations.slack import SlackAlertDestination

    channels = []

    def deliver(destination, alert):
        channels.append(destination.channel)
        return True

    monkeypatch.setattr(SlackAlertDestination, "send", deliver)
    manager = AlertManager()
    manager.register_destination(
        "slack", SlackAlertDestination("https://hooks.slack.com/test", channel="#default")
    )
    alert = Alert(title="T", message="M")
    assert manager.send_confirmed(alert, ["slack"], channel="#qa")
    assert manager.send_confirmed(alert, ["slack"], channel="#owner")
    assert manager.send(alert)
    assert not manager.send(alert, channel="#default")
    assert channels == ["#qa", "#owner", "#default"]


def test_quality_hook_preserves_event_identity(monkeypatch):
    import phlo_alerting.hooks_plugin as hooks

    manager = AlertManager()
    destination = MockDestination()
    manager.register_destination("test", destination)
    monkeypatch.setattr(hooks, "get_alert_manager", lambda: manager)
    handler = AlertingHookPlugin().get_hooks()[0].handler
    first = QualityResultEvent(
        event_type="quality.result",
        asset_key="orders",
        check_name="nulls",
        passed=False,
        correlation=HookCorrelation(run_id="run-1"),
    )
    second = QualityResultEvent(
        event_type="quality.result",
        asset_key="orders",
        check_name="nulls",
        passed=False,
        correlation=HookCorrelation(run_id="run-1"),
    )
    handler(first)
    handler(first)
    handler(second)
    assert len(destination.alerts) == 2
    assert [alert.event_id for alert in destination.alerts] == [first.event_id, second.event_id]
    assert all(alert.run_id == "run-1" for alert in destination.alerts)


def test_map_quality_severity():
    """Verifies quality status values map to expected alert severities."""
    assert _map_quality_severity(None) == AlertSeverity.ERROR
    assert _map_quality_severity("WARN") == AlertSeverity.WARNING
    assert _map_quality_severity("CRITICAL") == AlertSeverity.CRITICAL
    assert _map_quality_severity("FATAL") == AlertSeverity.CRITICAL
    assert _map_quality_severity("error") == AlertSeverity.ERROR


def test_map_telemetry_severity():
    """Verifies telemetry status values map to expected alert severities."""
    assert _map_telemetry_severity("critical") == AlertSeverity.CRITICAL
    assert _map_telemetry_severity("error") == AlertSeverity.ERROR


def test_alerting_hooks_plugin_registrations():
    """Verifies alerting plugin registers both quality and telemetry hooks."""
    plugin = AlertingHookPlugin()
    hooks = plugin.get_hooks()
    assert len(hooks) == 2
    names = {h.hook_name for h in hooks}
    assert "alerting_quality" in names
    assert "alerting_telemetry" in names


def test_alerting_settings_defaults():
    """Verifies default alerting settings values."""
    settings = AlertingSettings()
    assert settings.phlo_alert_email_smtp_port == 587
    assert settings.phlo_alert_email_recipients == []
    assert settings.phlo_alert_slack_webhook is None


def test_get_alert_manager_returns_singleton():
    """Verifies the package-root accessor returns the global AlertManager."""
    manager = get_alert_manager()

    assert isinstance(manager, AlertManager)
    assert get_alert_manager() is manager


def test_package_exports_public_surface():
    """Verifies phlo_alerting re-exports its public API at the package root."""
    assert hasattr(phlo_alerting, "Alert")
    assert hasattr(phlo_alerting, "AlertManager")
    assert hasattr(phlo_alerting, "AlertSeverity")
    assert hasattr(phlo_alerting, "get_alert_manager")
