"""Phlo Alerting package for multi-destination notification management.

This package provides a comprehensive alerting system for Phlo pipelines,
supporting multiple notification destinations including Slack, PagerDuty,
and Email. It integrates with the Phlo hook system to automatically trigger
alerts on quality check failures and telemetry events.

Examples:
    Basic usage:
        >>> from phlo_alerting import AlertManager, Alert, AlertSeverity
        >>> manager = AlertManager()
        >>> alert = Alert(
        ...     title="Pipeline Failed",
        ...     message="Data quality check failed",
        ...     severity=AlertSeverity.ERROR
        ... )
        >>> manager.send(alert)

"""

from phlo_alerting.manager import (
    Alert,
    AlertManager,
    AlertSeverity,
    get_alert_manager,
)
from phlo_alerting.settings import AlertingSettings, get_settings

__all__ = [
    "AlertManager",
    "Alert",
    "AlertSeverity",
    "AlertingSettings",
    "get_alert_manager",
    "get_settings",
]
