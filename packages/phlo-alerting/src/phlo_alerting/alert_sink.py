"""Neutral alert-sink wrapper over the alerting manager.

This module provides the AlertManagerSink class, which implements the
neutral alert-sink capability interface. It wraps the AlertManager to
provide a standardized way for external systems to send alerts through
phlo-alerting without direct dependencies on the manager internals.
"""

from __future__ import annotations

from datetime import datetime, timezone

from phlo_alerting.manager import Alert, AlertSeverity, get_alert_manager


class AlertManagerSink:
    """Expose phlo-alerting through the neutral alert-sink capability.

    This class implements the alert sink interface expected by the Phlo
    capability system. It translates external alert calls into the internal
    Alert format and routes them through the shared AlertManager. The class
    is stateless and delegates entirely to AlertManager.

    Examples:
            >>> sink = AlertManagerSink()
            >>> sink.send_alert(
            ...     title="Test Alert",
            ...     message="This is a test",
            ...     severity="error"
            ... )

    """

    def send_alert(
        self,
        *,
        title: str,
        message: str,
        severity: str | None = None,
        asset_name: str | None = None,
        run_id: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """Send one alert through the shared alert manager.

        Creates an Alert object from the provided parameters and routes it
        through the global AlertManager to all configured destinations.
        severity is a string level (info, warning, error, critical) that
        defaults to "error" when missing or invalid. Returns True when the
        alert reached at least one destination and False otherwise;
        exceptions from individual destinations are logged but not raised.

        Examples:
                    >>> sink = AlertManagerSink()
                    >>> result = sink.send_alert(
                    ...     title="Pipeline Error",
                    ...     message="ETL job failed",
                    ...     severity="critical",
                    ...     asset_name="sales_data",
                    ...     run_id="run_123"
                    ... )
                    >>> isinstance(result, bool)
                    True

        """
        alert = Alert(
            title=title,
            message=message,
            severity=_coerce_alert_severity(severity),
            asset_name=asset_name,
            run_id=run_id,
            error_message=error_message,
            timestamp=datetime.now(timezone.utc),
        )
        return get_alert_manager().send(alert)


def _coerce_alert_severity(severity: str | None) -> AlertSeverity:
    """Normalize string severity into the alerting enum.

    Converts string severity values into AlertSeverity enum values, handling
    case insensitivity and falling back to ERROR when the input is None,
    empty, or invalid.

    Examples:
        >>> _coerce_alert_severity("warning")
        <AlertSeverity.WARNING: 'warning'>
        >>> _coerce_alert_severity("CRITICAL")
        <AlertSeverity.CRITICAL: 'critical'>
        >>> _coerce_alert_severity(None)
        <AlertSeverity.ERROR: 'error'>
        >>> _coerce_alert_severity("invalid")
        <AlertSeverity.ERROR: 'error'>

    """
    if not severity:
        return AlertSeverity.ERROR
    normalized = severity.strip()
    if not normalized:
        return AlertSeverity.ERROR
    by_name = getattr(AlertSeverity, normalized.upper(), None)
    if by_name is not None:
        return by_name
    try:
        return AlertSeverity(normalized.lower())
    except ValueError:
        return AlertSeverity.ERROR
