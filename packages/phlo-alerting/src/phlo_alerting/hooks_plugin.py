"""Hook plugin for alerting on quality and telemetry events.

Implements the HookPlugin interface to trigger alerts on pipeline events:
a quality-result handler fires on failed checks and a telemetry handler
fires on error-level telemetry events.


Examples:
    The plugin is automatically discovered and registered by Phlo's
    plugin system. Manual usage is typically not required.

    >>> from phlo_alerting.hooks_plugin import AlertingHookPlugin
    >>> plugin = AlertingHookPlugin()
    >>> plugin.metadata.name
    'alerting'

"""

from __future__ import annotations

from typing import Any

from phlo.hooks import QualityResultEvent, TelemetryEvent
from phlo.logging import get_logger
from phlo.plugins.base import PluginMetadata
from phlo.plugins.hooks import HookFilter, HookPlugin, HookRegistration

from phlo_alerting.manager import Alert, AlertSeverity, get_alert_manager

logger = get_logger(__name__)


class AlertingHookPlugin(HookPlugin):
    """Emit alerts on failed quality checks and error-level telemetry events,
    routed through the AlertManager to configured destinations.

    Examples:
            >>> plugin = AlertingHookPlugin()
            >>> hooks = plugin.get_hooks()
            >>> len(hooks)
            2
            >>> hooks[0].hook_name
            'alerting_quality'

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Metadata for the alerting hook plugin.

        Examples:
            >>> plugin = AlertingHookPlugin()
            >>> meta = plugin.metadata
            >>> meta.name
            'alerting'
            >>> meta.version
            '0.1.0'

        """

        return PluginMetadata(
            name="alerting",
            version="0.1.0",
            description="Alerting hooks for quality and telemetry events",
        )

    def get_hooks(self) -> list[HookRegistration]:
        """Register handlers for quality and telemetry events.

        Examples:
            >>> plugin = AlertingHookPlugin()
            >>> hooks = plugin.get_hooks()
            >>> [h.hook_name for h in hooks]
            ['alerting_quality', 'alerting_telemetry']

        """

        return [
            HookRegistration(
                hook_name="alerting_quality",
                handler=self._handle_quality,
                filters=HookFilter(event_types={"quality.result"}),
            ),
            HookRegistration(
                hook_name="alerting_telemetry",
                handler=self._handle_telemetry,
                filters=HookFilter(event_types={"telemetry.log", "telemetry.metric"}),
            ),
        ]

    def _handle_quality(self, event: Any) -> None:
        """Send an alert for failed quality checks, mapping quality severity
        levels to alert severities; called automatically by the hook system on
        quality.result events.
        """

        if not isinstance(event, QualityResultEvent):
            return
        if event.passed:
            return
        severity = _map_quality_severity(event.severity)
        message = _format_quality_message(event)
        alert = Alert(
            title=f"Quality check failed: {event.check_name}",
            message=message,
            severity=severity,
            asset_name=event.asset_key,
        )
        logger.info(
            "alerting_quality_alert_send",
            event_type=event.event_type,
            asset_key=event.asset_key,
            check_name=event.check_name,
            quality_severity=event.severity,
            alert_severity=severity.value,
        )
        get_alert_manager().send(alert)

    def _handle_telemetry(self, event: Any) -> None:
        """Send an alert for TelemetryEvent instances with "error" or "critical"
        level, mapping telemetry levels to alert severities and taking the asset
        name from tags; called automatically by the hook system on telemetry.log
        and telemetry.metric events.
        """

        if not isinstance(event, TelemetryEvent):
            return
        if not event.level or event.level.lower() not in {"error", "critical"}:
            return
        alert = Alert(
            title=f"Telemetry {event.level} event: {event.name}",
            message=str(event.payload or event.value or ""),
            severity=_map_telemetry_severity(event.level),
            asset_name=event.tags.get("asset"),
        )
        logger.info(
            "alerting_telemetry_alert_send",
            event_type=event.event_type,
            event_name=event.name,
            level=event.level.lower(),
            asset_key=event.tags.get("asset"),
            alert_severity=alert.severity.value,
        )
        get_alert_manager().send(alert)


def _map_quality_severity(severity: str | None) -> AlertSeverity:
    """Map quality severity strings ("WARN", "CRITICAL", "FATAL") to alert
    severities, defaulting to ERROR for None or unknown values.

    Examples:
        >>> _map_quality_severity("WARN")
        <AlertSeverity.WARNING: 'warning'>
        >>> _map_quality_severity("CRITICAL")
        <AlertSeverity.CRITICAL: 'critical'>
        >>> _map_quality_severity(None)
        <AlertSeverity.ERROR: 'error'>
        >>> _map_quality_severity("unknown")
        <AlertSeverity.ERROR: 'error'>

    """

    if not severity:
        return AlertSeverity.ERROR
    value = severity.upper()
    if value == "WARN":
        return AlertSeverity.WARNING
    if value in {"CRITICAL", "FATAL"}:
        return AlertSeverity.CRITICAL
    return AlertSeverity.ERROR


def _map_telemetry_severity(level: str) -> AlertSeverity:
    """Map telemetry levels to alert severities: critical becomes CRITICAL,
    everything else ERROR.

    Examples:
            >>> _map_telemetry_severity("critical")
            <AlertSeverity.CRITICAL: 'critical'>
            >>> _map_telemetry_severity("error")
            <AlertSeverity.ERROR: 'error'>

    """

    value = level.lower()
    if value == "critical":
        return AlertSeverity.CRITICAL
    return AlertSeverity.ERROR


def _format_quality_message(event: QualityResultEvent) -> str:
    """Format a multi-line quality failure message with asset, check, partition
    key, and any error or failure details.

    Examples:
            >>> from phlo.hooks import QualityResultEvent
            >>> event = QualityResultEvent(
            ...     check_name="null_check",
            ...     asset_key="users_table",
            ...     passed=False,
            ...     partition_key="2024-01-01",
            ...     metadata={"error": "Null values found", "failure_message": "3 rows failed"}
            ... )
            >>> msg = _format_quality_message(event)
            >>> "Asset: users_table" in msg
            True
            >>> "Partition: 2024-01-01" in msg
            True

    """

    parts = [
        f"Asset: {event.asset_key}",
        f"Check: {event.check_name}",
    ]
    if event.partition_key:
        parts.append(f"Partition: {event.partition_key}")
    if event.metadata.get("error"):
        parts.append(f"Error: {event.metadata['error']}")
    if event.metadata.get("failure_message"):
        parts.append(f"Details: {event.metadata['failure_message']}")
    return "\n".join(parts)
