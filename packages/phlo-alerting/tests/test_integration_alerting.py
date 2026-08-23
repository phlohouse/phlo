"""Integration tests for phlo-alerting.

Covers AlertManager initialization, destination registration and dispatch,
deduplication of repeated alerts, targeted delivery to a single destination,
and the hooks plugin plus public package surface.
"""

import pytest

pytestmark = pytest.mark.integration


def test_alert_manager_initializes():
    """Test that AlertManager initializes correctly."""
    from phlo_alerting import AlertManager, get_alert_manager

    manager = get_alert_manager()
    assert isinstance(manager, AlertManager)


def test_alert_payload_defaults():
    """Test that Alert has correct default values."""
    from phlo_alerting import Alert, AlertSeverity

    alert = Alert(title="Test", message="hello")
    assert alert.severity == AlertSeverity.ERROR
    assert alert.timestamp is not None


def test_alert_severity_enum():
    """Test AlertSeverity enum values."""
    from phlo_alerting import AlertSeverity

    assert AlertSeverity.INFO.value == "info"
    assert AlertSeverity.WARNING.value == "warning"
    assert AlertSeverity.ERROR.value == "error"
    assert AlertSeverity.CRITICAL.value == "critical"


def test_alert_destination_registration():
    """Test custom destination registration."""
    from phlo_alerting.manager import AlertManager, AlertDestination, Alert

    class MockDestination(AlertDestination):
        """Mock alert destination for destination registration tests."""

        def __init__(self):
            """Initialize destination with captured alert storage."""
            self.alerts = []

        def send(self, alert: Alert) -> bool:
            """Store an alert and report success. Returns ``True``."""
            self.alerts.append(alert)
            return True

    manager = AlertManager()
    mock_dest = MockDestination()
    manager.register_destination("mock", mock_dest)

    assert "mock" in manager.destinations
    assert manager.destinations["mock"] is mock_dest


def test_alert_sending_to_destination():
    """Test sending alerts to registered destinations."""
    from phlo_alerting.manager import AlertManager, AlertDestination, Alert, AlertSeverity

    class MockDestination(AlertDestination):
        """Mock destination used to verify send behavior."""

        def __init__(self):
            """Initialize destination with captured alert storage."""
            self.alerts = []

        def send(self, alert: Alert) -> bool:
            """Store an alert and report success. Returns ``True``."""
            self.alerts.append(alert)
            return True

    manager = AlertManager()
    mock_dest = MockDestination()
    manager.register_destination("mock", mock_dest)

    alert = Alert(
        title="Test Alert",
        message="This is a test",
        severity=AlertSeverity.WARNING,
        asset_name="test_asset",
    )

    result = manager.send(alert)

    assert result is True
    assert len(mock_dest.alerts) == 1
    assert mock_dest.alerts[0].title == "Test Alert"


def test_alert_deduplication():
    """Test that duplicate alerts are not sent."""
    from phlo_alerting.manager import AlertManager, AlertDestination, Alert, AlertSeverity

    class MockDestination(AlertDestination):
        """Mock destination used for deduplication behavior checks."""

        def __init__(self):
            """Initialize destination with captured alert storage."""
            self.alerts = []

        def send(self, alert: Alert) -> bool:
            """Store an alert and report success. Returns ``True``."""
            self.alerts.append(alert)
            return True

    manager = AlertManager()
    mock_dest = MockDestination()
    manager.register_destination("mock", mock_dest)

    alert = Alert(
        title="Duplicate Test",
        message="Same alert",
        severity=AlertSeverity.ERROR,
        asset_name="dup_asset",
        error_message="same error",
    )

    # First send should succeed
    result1 = manager.send(alert)
    assert result1 is True
    assert len(mock_dest.alerts) == 1

    # Second send (duplicate) should be skipped
    result2 = manager.send(alert)
    assert result2 is False
    assert len(mock_dest.alerts) == 1  # Still only 1


def test_alert_to_specific_destination():
    """Test sending to specific destinations only."""
    from phlo_alerting.manager import AlertManager, AlertDestination, Alert

    class MockDestination(AlertDestination):
        """Mock destination keyed by name for routing tests."""

        def __init__(self, name):
            """Initialize the destination with the given identifier."""
            self.name = name
            self.alerts = []

        def send(self, alert: Alert) -> bool:
            """Store an alert and report success. Returns ``True``."""
            self.alerts.append(alert)
            return True

    manager = AlertManager()
    dest1 = MockDestination("dest1")
    dest2 = MockDestination("dest2")
    manager.register_destination("dest1", dest1)
    manager.register_destination("dest2", dest2)

    alert = Alert(title="Specific Test", message="Only to dest1")

    # Send only to dest1
    result = manager.send(alert, destinations=["dest1"])

    assert result is True
    assert len(dest1.alerts) == 1
    assert len(dest2.alerts) == 0


def test_alerting_hooks_plugin_exists():
    """Test that hooks plugin is properly defined."""
    from phlo_alerting.hooks_plugin import AlertingHookPlugin

    plugin = AlertingHookPlugin()
    assert plugin is not None
    # Check it has required hook method(s)
    assert hasattr(plugin, "get_hooks")
    assert hasattr(plugin, "metadata")


def test_alerting_exports():
    """Test that phlo-alerting exports required classes."""
    import phlo_alerting

    assert hasattr(phlo_alerting, "Alert")
    assert hasattr(phlo_alerting, "AlertManager")
    assert hasattr(phlo_alerting, "AlertSeverity")
    assert hasattr(phlo_alerting, "get_alert_manager")
