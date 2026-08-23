"""PagerDuty alert destination.

This module provides PagerDutyAlertDestination, which sends alerts to
PagerDuty via the Events API v2. Supports severity mapping and alert
deduplication through custom dedup keys.

Configuration is typically loaded from environment variables:
    PHLO_ALERT_PAGERDUTY_KEY: PagerDuty Events API v2 integration key

Examples:
    Basic usage:
        >>> from phlo_alerting.destinations.pagerduty import PagerDutyAlertDestination
        >>> dest = PagerDutyAlertDestination(
        ...     integration_key="abcdef1234567890abcdef1234567890"
        ... )
        >>> dest.send(alert)
        True

    The integration key can be obtained from a PagerDuty service's
    integration settings. Events API v2 must be enabled.

See Also:
    manager.AlertDestination: Base class defining the interface.
    settings.AlertingSettings: Configuration model for PagerDuty settings.
    PagerDuty Events API v2 documentation: https://developer.pagerduty.com/docs/events-api-v2

"""

from __future__ import annotations

import requests

from phlo.logging import get_logger
from phlo_alerting.manager import Alert, AlertDestination, AlertSeverity

logger = get_logger(__name__)


class PagerDutyAlertDestination(AlertDestination):
    """Send alerts to PagerDuty via Events API.

        Concrete implementation of AlertDestination that creates PagerDuty
    incidents via the Events API v2. Maps internal severity levels to
    PagerDuty severities and provides deduplication through custom keys.

    Examples:
            >>> dest = PagerDutyAlertDestination(
            ...     integration_key="abcdef1234567890abcdef1234567890"
            ... )
            >>> isinstance(dest, AlertDestination)
            True
            >>> dest.api_url
            'https://events.pagerduty.com/v2/enqueue'

    """

    def __init__(self, integration_key: str):
        """Initialize the destination with a PagerDuty Events API v2 integration key;
        the key identifies the PagerDuty service that receives the alerts and is
        validated by PagerDuty during send().

            Examples:
                    >>> dest = PagerDutyAlertDestination("abcdef1234567890abcdef1234567890")
                    >>> dest.integration_key
                    'abcdef1234567890abcdef1234567890'
        """
        self.integration_key = integration_key
        self.api_url = "https://events.pagerduty.com/v2/enqueue"

    def send(self, alert: Alert) -> bool:
        """Send alert to PagerDuty.

                Posts the alert as an incident trigger event and returns True when
        PagerDuty accepts it (HTTP 202), False otherwise. Network and API errors
        are caught and logged rather than raised.

        Examples:
                    >>> from phlo_alerting.manager import Alert, AlertSeverity
                    >>> alert = Alert(
                    ...     title="Critical System Failure",
                    ...     message="Database connection lost",
                    ...     severity=AlertSeverity.CRITICAL,
                    ...     asset_name="prod_db"
                    ... )
                    >>> result = dest.send(alert)
                    >>> isinstance(result, bool)
                    True
        """
        try:
            payload = self._build_payload(alert)
            response = requests.post(self.api_url, json=payload, timeout=10)
            return response.status_code == 202  # Accepted
        except Exception:
            logger.exception(
                "pagerduty_alert_send_failed",
                alert_title=alert.title,
                severity=alert.severity.value,
                asset_name=alert.asset_name,
                run_id=alert.run_id,
            )
            return False

    def _build_payload(self, alert: Alert) -> dict:
        """Build PagerDuty event payload.

                Constructs an Events API v2 payload with proper severity mapping,
        custom details, and a deduplication key that groups related alerts to
        prevent incident spam.

        Examples:
                    >>> from phlo_alerting.manager import Alert, AlertSeverity
                    >>> alert = Alert(
                    ...     title="Test Incident",
                    ...     message="Test description",
                    ...     severity=AlertSeverity.ERROR,
                    ...     asset_name="test_service",
                    ...     run_id="run_123"
                    ... )
                    >>> payload = dest._build_payload(alert)
                    >>> payload["event_action"]
                    'trigger'
                    >>> payload["payload"]["severity"]
                    'error'
        """
        # Map severity to PagerDuty severity
        severity_map = {
            AlertSeverity.INFO: "info",
            AlertSeverity.WARNING: "warning",
            AlertSeverity.ERROR: "error",
            AlertSeverity.CRITICAL: "critical",
        }

        pd_severity = severity_map.get(alert.severity, "error")

        # Build custom details
        custom_details = {
            "severity": alert.severity.value,
            "message": alert.message,
            "timestamp": alert.timestamp.isoformat() if alert.timestamp else None,
        }

        if alert.asset_name:
            custom_details["asset"] = alert.asset_name

        if alert.run_id:
            custom_details["run_id"] = alert.run_id

        if alert.error_message:
            custom_details["error"] = alert.error_message

        # Generate dedup key for alert grouping
        dedup_key = f"phlo-{alert.asset_name or 'unknown'}-{alert.run_id or 'unknown'}"

        payload = {
            "routing_key": self.integration_key,
            "event_action": "trigger",
            "dedup_key": dedup_key,
            "payload": {
                "summary": alert.title,
                "severity": pd_severity,
                "source": "Phlo",
                "timestamp": alert.timestamp.isoformat() if alert.timestamp else None,
                "custom_details": custom_details,
            },
        }

        return payload
