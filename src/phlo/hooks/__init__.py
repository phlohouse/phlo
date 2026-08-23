"""Hook event system for Phlo plugins.

Plugins emit events during lifecycle stages (ingestion, transformation, quality
checks) and other plugins register handlers on the shared hook bus to react to them.
Supported event types cover ingestion start/end, transform start/end, quality check
results, service start/stop, and schema/data migrations.

Exports are resolved lazily via ``__getattr__`` to avoid circular imports during
plugin discovery.

Example:
    ```python
    from phlo.hooks import get_hook_bus, IngestionEventEmitter, IngestionEventContext

    # Get the global hook bus
    bus = get_hook_bus()

    # Create an emitter for ingestion events
    context = IngestionEventContext(
        asset_key="my_table",
        table_name="my_table",
        group_name="ingestion"
    )
    emitter = IngestionEventEmitter(context)

    # Emit events during your operation
    emitter.emit_start()
    # ... perform ingestion ...
    emitter.emit_end(status="success")
    ```
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

__all__ = [
    "EVENT_VERSION",
    "HookBus",
    "HookCorrelation",
    "HookEvent",
    "IngestionEventContext",
    "IngestionEventEmitter",
    "DataMigrationEventContext",
    "DataMigrationEventEmitter",
    "LineageEventContext",
    "LineageEventEmitter",
    "PublishEventContext",
    "PublishEventEmitter",
    "QualityResultEventContext",
    "QualityResultEventEmitter",
    "SchemaMigrationEventContext",
    "SchemaMigrationEventEmitter",
    "ServiceLifecycleEventContext",
    "ServiceLifecycleEventEmitter",
    "TelemetryEventContext",
    "TelemetryEventEmitter",
    "TransformEventContext",
    "TransformEventEmitter",
    "IngestionEvent",
    "DataMigrationEvent",
    "LineageEvent",
    "PublishEvent",
    "QualityResultEvent",
    "SchemaMigrationEvent",
    "ServiceLifecycleEvent",
    "TelemetryEvent",
    "TransformEvent",
    "RunEvidenceObservationEvent",
    "LogEvent",
    "get_hook_bus",
]

_BUS_EXPORTS = {"HookBus", "get_hook_bus"}
_EMITTER_EXPORTS = {
    "IngestionEventContext",
    "IngestionEventEmitter",
    "DataMigrationEventContext",
    "DataMigrationEventEmitter",
    "LineageEventContext",
    "LineageEventEmitter",
    "PublishEventContext",
    "PublishEventEmitter",
    "QualityResultEventContext",
    "QualityResultEventEmitter",
    "SchemaMigrationEventContext",
    "SchemaMigrationEventEmitter",
    "ServiceLifecycleEventContext",
    "ServiceLifecycleEventEmitter",
    "TelemetryEventContext",
    "TelemetryEventEmitter",
    "TransformEventContext",
    "TransformEventEmitter",
}
_EVENT_EXPORTS = {
    "EVENT_VERSION",
    "HookCorrelation",
    "HookEvent",
    "IngestionEvent",
    "DataMigrationEvent",
    "LineageEvent",
    "PublishEvent",
    "QualityResultEvent",
    "SchemaMigrationEvent",
    "ServiceLifecycleEvent",
    "TelemetryEvent",
    "TransformEvent",
    "RunEvidenceObservationEvent",
    "LogEvent",
}


if TYPE_CHECKING:
    from phlo.hooks.bus import HookBus, get_hook_bus
    from phlo.hooks.emitters import (
        DataMigrationEventContext,
        DataMigrationEventEmitter,
        IngestionEventContext,
        IngestionEventEmitter,
        LineageEventContext,
        LineageEventEmitter,
        PublishEventContext,
        PublishEventEmitter,
        QualityResultEventContext,
        QualityResultEventEmitter,
        SchemaMigrationEventContext,
        SchemaMigrationEventEmitter,
        ServiceLifecycleEventContext,
        ServiceLifecycleEventEmitter,
        TelemetryEventContext,
        TelemetryEventEmitter,
        TransformEventContext,
        TransformEventEmitter,
    )
    from phlo.hooks.events import (
        EVENT_VERSION,
        DataMigrationEvent,
        HookCorrelation,
        HookEvent,
        IngestionEvent,
        LineageEvent,
        LogEvent,
        PublishEvent,
        QualityResultEvent,
        SchemaMigrationEvent,
        ServiceLifecycleEvent,
        TelemetryEvent,
        TransformEvent,
    )


def __getattr__(name: str):  # noqa: ANN001
    """Lazily resolve exports from hook submodules.

    Raises: AttributeError when the name is not an exported hook symbol.
    """
    if name in _BUS_EXPORTS:
        _bus = import_module("phlo.hooks.bus")
        return getattr(_bus, name)
    if name in _EMITTER_EXPORTS:
        _emitters = import_module("phlo.hooks.emitters")
        return getattr(_emitters, name)
    if name in _EVENT_EXPORTS:
        _events = import_module("phlo.hooks.events")
        return getattr(_events, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Return module attribute names for introspection tools."""
    return sorted(set(__all__))
