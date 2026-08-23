"""Alert manager for sending notifications to multiple destinations.

This module provides the core alerting infrastructure for Phlo, including
severity levels, alert data structures, destination management, and
deduplication logic. It supports multiple alert destinations (Slack,
PagerDuty, Email) with automatic registration based on configuration.

Examples:
    Sending an alert:
        >>> from phlo_alerting.manager import AlertManager, Alert, AlertSeverity
        >>> manager = AlertManager()
        >>> alert = Alert(
        ...     title="Data Quality Issue",
        ...     message="Null values detected",
        ...     severity=AlertSeverity.WARNING,
        ...     asset_name="customer_table"
        ... )
        >>> manager.send(alert)

    Registering a custom destination:
        >>> from phlo_alerting.manager import AlertDestination
        >>> class CustomDestination(AlertDestination):
        ...     def send(self, alert):
        ...         print(f"Alert: {alert.title}")
        ...         return True
        >>> manager.register_destination("custom", CustomDestination())

"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from phlo.logging import get_logger

logger = get_logger(__name__)


class AlertSeverity(str, Enum):
    """Alert severity levels.

    Standard severity levels for Phlo alerts; they drive visual styling,
    routing logic, and escalation policies across notification destinations.

    Examples:
        >>> AlertSeverity.INFO
        <AlertSeverity.INFO: 'info'>
        >>> AlertSeverity.ERROR.value
        'error'
        >>> AlertSeverity("warning")
        <AlertSeverity.WARNING: 'warning'>

    """

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(slots=True)
class Alert:
    """Alert payload data structure.

    Single alert with all metadata needed for routing and display across
    notification destinations.

    Examples:
        >>> alert = Alert(
        ...     title="Pipeline Failed",
        ...     message="ETL job encountered an error",
        ...     severity=AlertSeverity.CRITICAL,
        ...     asset_name="daily_etl",
        ...     run_id="run_2024_001"
        ... )
        >>> alert.title
        'Pipeline Failed'
        >>> alert.timestamp is not None
        True

    """

    title: str
    message: str
    severity: AlertSeverity = AlertSeverity.ERROR
    asset_name: Optional[str] = None
    run_id: Optional[str] = None
    error_message: Optional[str] = None
    timestamp: Optional[datetime] = None

    def __post_init__(self) -> None:
        """Set default timestamp if not provided.

        Examples:
            >>> alert = Alert(title="Test", message="Test message")
            >>> alert.timestamp is not None
            True

        """
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


class AlertDestination:
    """Base class for alert destinations.

    Abstract base defining the interface for all alert destinations;
    concrete implementations override send() with destination-specific
    delivery logic.

    Examples:
        >>> class ConsoleDestination(AlertDestination):
        ...     def send(self, alert):
        ...         print(f"[{alert.severity.value.upper()}] {alert.title}")
        ...         return True
        >>> dest = ConsoleDestination()
        >>> alert = Alert(title="Test", message="Hello")
        >>> dest.send(alert)
        [ERROR] Test
        True

    """

    def send(self, alert: Alert) -> bool:
        """Send an alert to this destination.

        Abstract; concrete destinations deliver through their channel.
        Returns True if the alert was successfully delivered, False otherwise.
        Raises: NotImplementedError when called on the base class directly.

        Examples:
            See subclass implementations in destinations/ directory.

        """
        raise NotImplementedError


class AlertManager:
    """Manage alert destinations and deduplication.

    Central manager for the alerting system: maintains a registry of
    destinations, deduplicates to prevent alert spam, and routes alerts
    based on configuration.

    Dedup keys are never expired, so duplicates are suppressed for the
    manager's lifetime regardless of _dedup_window_minutes.

    Examples:
            >>> manager = AlertManager()
            >>> manager.destinations
            {}
            >>> manager._dedup_window_minutes
            60

    """

    def __init__(self):
        """Initialize an empty alert manager.

        Examples:
            >>> manager = AlertManager()
            >>> len(manager.destinations)
            0

        """
        self.destinations: dict[str, AlertDestination] = {}
        self._sent_alerts: set[str] = set()
        self._dedup_window_minutes = 60

    def register_destination(self, name: str, destination: AlertDestination) -> None:
        """Register an alert destination under a unique name.

        Once registered, the destination receives all alerts sent through the
        manager unless specific destinations are requested. Registering an
        existing name overwrites the previous destination.

        Examples:
            >>> from phlo_alerting.destinations.slack import SlackAlertDestination
            >>> manager = AlertManager()
            >>> slack = SlackAlertDestination("https://hooks.slack.com/test")
            >>> manager.register_destination("slack", slack)
            >>> "slack" in manager.destinations
            True

        """
        self.destinations[name] = destination
        logger.info("alert_destination_registered", destination_name=name)

    def send(self, alert: Alert, destinations: Optional[list[str]] = None) -> bool:
        """Send an alert to specified or all registered destinations.

        Returns True if the alert was delivered to at least one destination;
        False if all destinations failed or the alert was deduplicated.
        Individual destination failures are logged but do not raise.

        Examples:
                    >>> manager = AlertManager()
                    >>> alert = Alert(title="Test", message="Hello")
                    >>> # Without destinations, returns False
                    >>> manager.send(alert)
                    False

        """
        # Check for duplicates
        alert_key = self._get_alert_key(alert)
        if self._is_duplicate(alert_key):
            logger.debug("alert_duplicate_skipped", alert_key=alert_key)
            return False

        # Determine which destinations to use
        targets = destinations or list(self.destinations.keys())

        # Send to each destination
        sent = False
        for dest_name in targets:
            if dest_name not in self.destinations:
                logger.warning("alert_unknown_destination", destination_name=dest_name)
                continue

            try:
                dest = self.destinations[dest_name]
                if dest.send(alert):
                    sent = True
                    logger.info(
                        "alert_sent",
                        destination_name=dest_name,
                        alert_title=alert.title,
                    )
            except Exception:
                logger.exception("alert_send_failed", destination_name=dest_name)

        # Mark as sent
        if sent:
            self._sent_alerts.add(alert_key)

        return sent

    def _get_alert_key(self, alert: Alert) -> str:
        """Build the dedup key from asset name, error message, and severity.

        Examples:
            >>> from phlo_alerting.manager import AlertManager, Alert, AlertSeverity
            >>> manager = AlertManager()
            >>> alert = Alert(
            ...     title="Test",
            ...     message="Test msg",
            ...     asset_name="asset1",
            ...     error_message="error1",
            ...     severity=AlertSeverity.ERROR
            ... )
            >>> key = manager._get_alert_key(alert)
            >>> key
            'asset1:error1:error'

        """
        return f"{alert.asset_name}:{alert.error_message}:{alert.severity.value}"

    def _is_duplicate(self, key: str) -> bool:
        """Check whether an alert with this dedup key was already sent.

        Dedup keys never expire, so duplicates stay suppressed for the
        manager's lifetime.

        Examples:
            >>> manager = AlertManager()
            >>> manager._is_duplicate("test_key")
            False
            >>> manager._sent_alerts.add("test_key")
            >>> manager._is_duplicate("test_key")
            True

        """
        return key in self._sent_alerts


# Global alert manager instance
_alert_manager: Optional[AlertManager] = None


def get_alert_manager() -> AlertManager:
    """Get or create the global alert manager singleton.

    On first creation, automatically registers default destinations based
    on environment configuration.

    Examples:
        >>> manager1 = get_alert_manager()
        >>> manager2 = get_alert_manager()
        >>> manager1 is manager2
        True

    """
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager()
        _register_default_destinations(_alert_manager)
    return _alert_manager


def _register_default_destinations(manager: AlertManager) -> None:
    """Register default alert destinations from environment configuration.

    Supports Slack (PHLO_ALERT_SLACK_WEBHOOK), PagerDuty
    (PHLO_ALERT_PAGERDUTY_KEY), and Email (PHLO_ALERT_EMAIL_*).

    Examples:
        This function is called automatically by get_alert_manager() and
        should not typically be called directly.

    """
    from phlo_alerting.destinations.email import EmailAlertDestination
    from phlo_alerting.destinations.pagerduty import PagerDutyAlertDestination
    from phlo_alerting.destinations.slack import SlackAlertDestination
    from phlo_alerting.settings import get_settings

    config = get_settings()

    # Register Slack if configured
    if config.phlo_alert_slack_webhook:
        try:
            slack = SlackAlertDestination(
                webhook_url=config.phlo_alert_slack_webhook,
                channel=config.phlo_alert_slack_channel,
            )
            manager.register_destination("slack", slack)
        except Exception:
            logger.warning(
                "alert_destination_register_failed", destination_name="slack", exc_info=True
            )

    # Register PagerDuty if configured
    if config.phlo_alert_pagerduty_key:
        try:
            pagerduty = PagerDutyAlertDestination(integration_key=config.phlo_alert_pagerduty_key)
            manager.register_destination("pagerduty", pagerduty)
        except Exception:
            logger.warning(
                "alert_destination_register_failed",
                destination_name="pagerduty",
                exc_info=True,
            )

    # Register Email if configured
    if config.phlo_alert_email_smtp_host:
        try:
            email = EmailAlertDestination(
                smtp_host=config.phlo_alert_email_smtp_host,
                smtp_port=config.phlo_alert_email_smtp_port,
                smtp_user=config.phlo_alert_email_smtp_user,
                smtp_password=config.phlo_alert_email_smtp_password,
                recipients=config.phlo_alert_email_recipients,
            )
            manager.register_destination("email", email)
        except Exception:
            logger.warning(
                "alert_destination_register_failed", destination_name="email", exc_info=True
            )
