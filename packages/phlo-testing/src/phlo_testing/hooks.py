"""Hook testing utilities for Phlo workflows.

Provides mock implementations and helpers for testing the Phlo hook system,
including event capture, sample event generation, and mock hook bus for
isolated testing.

Example:
    >>> from phlo_testing.hooks import MockHookBus, capture_events, sample_ingestion_event
    >>> bus = MockHookBus()
    >>> captured = capture_events(bus=bus, event_types=["ingestion.end"])
    >>> bus.emit(sample_ingestion_event())
    >>> assert len(captured.events) == 1


Contributes hook handlers through the phlo.plugins.hooks extension surface rather than
direct import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from phlo.hooks import (
    HookEvent,
    IngestionEvent,
    LineageEvent,
    PublishEvent,
    QualityResultEvent,
    ServiceLifecycleEvent,
    TelemetryEvent,
    TransformEvent,
)
from phlo.hooks.bus import HookBus
from phlo.plugins.hooks import HookFilter, HookRegistration


class MockHookBus(HookBus):
    """Hook bus that bypasses plugin discovery so tests can register and emit
    events in isolation.

    Example:
        >>> bus = MockHookBus()
        >>> bus.register(HookRegistration(
        ...     hook_name="test",
        ...     handler=lambda e: print(e),
        ... ))

    """

    def _ensure_discovered(self) -> None:
        """Override discovery to skip plugin loading."""
        self._discovered = True


@dataclass
class CapturedEvents:
    """Capture hook events in memory for assertions about event sequences.

    Example:
        >>> captured = CapturedEvents(events=[])
        >>> captured.handler(sample_ingestion_event())
        >>> assert len(captured.events) == 1

    """

    events: list[HookEvent]

    def handler(self, event: HookEvent) -> None:
        """Append a hook event to the captured list."""
        self.events.append(event)


def capture_events(
    *,
    bus: HookBus,
    event_types: Iterable[str] | None = None,
) -> CapturedEvents:
    """Register a capture handler on the hook bus, optionally filtered to
    specific event types, and return the collected-events container.

    Example:
        >>> bus = MockHookBus()
        >>> captured = capture_events(
        ...     bus=bus,
        ...     event_types=["ingestion.end", "quality.result"]
        ... )
        >>> bus.emit(sample_ingestion_event())
        >>> assert len(captured.events) == 1

    """
    captured = CapturedEvents(events=[])
    filters = HookFilter(event_types=set(event_types)) if event_types else None
    bus.register(
        HookRegistration(
            hook_name="capture_events",
            handler=captured.handler,
            filters=filters,
        ),
        plugin_name="phlo-testing",
    )
    return captured


def sample_ingestion_event() -> IngestionEvent:
    """Return a pre-configured successful IngestionEvent for tests.

    Example:
        >>> event = sample_ingestion_event()
        >>> assert event.event_type == "ingestion.end"
        >>> assert event.status == "success"

    """
    return IngestionEvent(
        event_type="ingestion.end",
        asset_key="dlt_sample",
        table_name="bronze.sample",
        group_name="sample",
        partition_key="2024-01-01",
        status="success",
    )


def sample_quality_event() -> QualityResultEvent:
    """Return a pre-configured passed QualityResultEvent for tests.

    Example:
        >>> event = sample_quality_event()
        >>> assert event.event_type == "quality.result"
        >>> assert event.passed is True

    """
    return QualityResultEvent(
        event_type="quality.result",
        asset_key="sample_asset",
        check_name="null_check",
        passed=True,
        check_type="NullCheck",
    )


def sample_transform_event() -> TransformEvent:
    """Return a pre-configured successful dbt TransformEvent for tests.

    Example:
        >>> event = sample_transform_event()
        >>> assert event.event_type == "transform.end"
        >>> assert event.tool == "dbt"

    """
    return TransformEvent(
        event_type="transform.end",
        tool="dbt",
        status="success",
    )


def sample_publish_event() -> PublishEvent:
    """Return a pre-configured successful PublishEvent to Postgres for tests.

    Example:
        >>> event = sample_publish_event()
        >>> assert event.event_type == "publish.end"
        >>> assert event.target_system == "postgres"

    """
    return PublishEvent(
        event_type="publish.end",
        asset_key="publish_sample_marts",
        target_system="postgres",
        tables={"sample": "marts.sample"},
        status="success",
    )


def sample_lineage_event() -> LineageEvent:
    """Return a pre-configured raw-to-marts LineageEvent for tests.

    Example:
        >>> event = sample_lineage_event()
        >>> assert event.event_type == "lineage.edges"
        >>> assert ("raw.sample", "marts.sample") in event.edges

    """
    return LineageEvent(
        event_type="lineage.edges",
        edges=[("raw.sample", "marts.sample")],
    )


def sample_telemetry_event() -> TelemetryEvent:
    """Return a pre-configured TelemetryEvent metric for tests.

    Example:
        >>> event = sample_telemetry_event()
        >>> assert event.event_type == "telemetry.metric"
        >>> assert event.name == "sample_metric"

    """
    return TelemetryEvent(
        event_type="telemetry.metric",
        name="sample_metric",
        value=1,
    )


def sample_service_event() -> ServiceLifecycleEvent:
    """Return a pre-configured service startup ServiceLifecycleEvent for tests.

    Example:
        >>> event = sample_service_event()
        >>> assert event.event_type == "service.post_start"
        >>> assert event.service_name == "postgres"

    """
    return ServiceLifecycleEvent(
        event_type="service.post_start",
        service_name="postgres",
        phase="post_start",
        status="success",
    )
