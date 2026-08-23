"""Built-in telemetry hook providers.

CoreTelemetryHookProvider subscribes to telemetry.log and telemetry.metric
events and forwards TelemetryEvent instances into a module-local
TelemetryRecorder; non-telemetry events are silently ignored.
"""

from __future__ import annotations

from typing import Any

from phlo.capabilities.telemetry import TelemetryRecorder
from phlo.hooks.events import TelemetryEvent
from phlo.plugins.hooks import HookFilter, HookRegistration


class CoreTelemetryHookProvider:
    """Record generic telemetry events without an external package."""

    def __init__(self) -> None:
        self._recorder = TelemetryRecorder()

    def get_hooks(self) -> list[HookRegistration]:
        """Register the core telemetry hook for log and metric events."""
        return [
            HookRegistration(
                hook_name="core_telemetry",
                handler=self._handle_telemetry,
                filters=HookFilter(event_types={"telemetry.log", "telemetry.metric"}),
            )
        ]

    def _handle_telemetry(self, event: Any) -> None:
        if isinstance(event, TelemetryEvent):
            self._recorder.record(event)
