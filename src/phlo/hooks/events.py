"""Hook event payload definitions for the Phlo plugin system.

This module defines the dataclass-based event payloads used throughout the
hook system. Each event type inherits from :class:`HookEvent` and adds
specific fields relevant to its lifecycle stage.

Event payloads are immutable dataclasses with correlation tracking for
observability and debugging. All events include:
    - Event type identifier
    - Version for schema evolution
    - UTC timestamp
    - Optional tags for categorization
    - Correlation fields for distributed tracing

Key Event Types:
    - :class:`IngestionEvent`: Data ingestion lifecycle
    - :class:`TransformEvent`: dbt transformation lifecycle
    - :class:`QualityResultEvent`: Data quality check results
    - :class:`ServiceLifecycleEvent`: Service start/stop events
    - :class:`SchemaMigrationEvent`: Schema change tracking
    - :class:`DataMigrationEvent`: Data migration tracking
    - :class:`LineageEvent`: Asset lineage tracking
    - :class:`PublishEvent`: Data publication events
    - :class:`TelemetryEvent`: Metrics and logging events
    - :class:`LogEvent`: Structured log records

Example:
    ```python
    from phlo.hooks.events import IngestionEvent, HookCorrelation
    from datetime import UTC, datetime

    event = IngestionEvent(
        event_type="ingestion.start",
        asset_key="users.raw",
        table_name="users",
        group_name="raw_data",
        correlation=HookCorrelation(
            trace_id="abc-123",
            run_id="run-456"
        )
    )
    ```

"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from phlo._attempt import normalize_attempt

EVENT_VERSION = "1.0"


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(UTC)


@dataclass(kw_only=True)
class HookCorrelation:
    """Shared correlation fields for cross-signal observability.

    These fields are propagated through the event chain so distributed
    tracing and request tracking survive across services: request_id,
    OpenTelemetry trace_id/span_id/trace_flags, Dagster run_id/asset_key/
    job_name/partition_key, and the quality check_name.

    Example:
        ```python
        correlation = HookCorrelation(
            trace_id="abc-def-123",
            run_id="run-2024-001",
            asset_key="raw.users"
        )
        ```

    """

    request_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    trace_flags: str | None = None
    project_id: str | None = None
    run_id: str | None = None
    attempt: int = 1
    asset_key: str | None = None
    job_name: str | None = None
    partition_key: str | None = None
    check_name: str | None = None

    def __post_init__(self) -> None:
        self.attempt = normalize_attempt(self.attempt)


@dataclass(kw_only=True)
class HookEvent:
    """Base event payload shared by all hook events, providing routing,
    schema versioning, and correlation tracking. All events are immutable
    dataclasses created with kw_only=True to force explicit field naming.

    Example:
        ```python
        from phlo.hooks.events import HookEvent, HookCorrelation

        event = HookEvent(
            event_type="custom.event",
            tags={"environment": "production"},
            correlation=HookCorrelation(trace_id="abc-123")
        )
        ```

    """

    event_type: str
    version: str = EVENT_VERSION
    event_id: str = field(default_factory=lambda: str(uuid4()))
    producer: str = "phlo"
    timestamp: datetime = field(default_factory=_utc_now)
    tags: dict[str, str] = field(default_factory=dict)
    correlation: HookCorrelation = field(default_factory=HookCorrelation)


@dataclass(kw_only=True)
class ServiceLifecycleEvent(HookEvent):
    """Lifecycle event emitted around service start/stop phases for
    Phlo-managed services (PostgreSQL, MinIO, Trino, ...). Carries the
    service/project identity, container name, phase, status, and
    service-specific metadata.

    Event Types:
        - ``service.start``: Service is starting
        - ``service.stop``: Service is stopping
        - ``service.configure``: Configuration applied

    Example:
        ```python
        from phlo.hooks.events import ServiceLifecycleEvent

        event = ServiceLifecycleEvent(
            event_type="service.start",
            service_name="postgres",
            phase="start",
            status="started",
            metadata={"version": "15.2"}
        )
        ```

    """

    service_name: str
    project_name: str | None = None
    project_root: str | None = None
    container_name: str | None = None
    phase: str | None = None
    status: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(kw_only=True)
class IngestionEvent(HookEvent):
    """Event emitted at the start and end of ingestion runs from sources into
    the lakehouse, capturing status, performance metrics (rows processed,
    bytes written, duration), and errors.

    Event Types:
        - ``ingestion.start``: Ingestion operation is beginning
        - ``ingestion.end``: Ingestion operation completed
        - ``ingestion.progress``: Periodic progress updates (optional)

    Example:
        ```python
        from phlo.hooks.events import IngestionEvent, HookCorrelation

        # Start event
        start_event = IngestionEvent(
            event_type="ingestion.start",
            asset_key="raw.users",
            table_name="users",
            group_name="raw",
            correlation=HookCorrelation(run_id="run-123")
        )

        # End event with metrics
        end_event = IngestionEvent(
            event_type="ingestion.end",
            asset_key="raw.users",
            table_name="users",
            group_name="raw",
            status="success",
            metrics={"rows_processed": 10000, "duration_seconds": 45.2},
            correlation=HookCorrelation(run_id="run-123")
        )
        ```

    """

    asset_key: str
    table_name: str
    group_name: str
    partition_key: str | None = None
    run_id: str | None = None
    branch_name: str | None = None
    status: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(kw_only=True)
class TransformEvent(HookEvent):
    """Event emitted for dbt transformation runs, recording the tool, project
    directory, target environment, executed model names, final status, and
    performance metrics.

    Event Types:
        - ``transform.start``: Transformation run is beginning
        - ``transform.end``: Transformation run completed

    Example:
        ```python
        from phlo.hooks.events import TransformEvent

        event = TransformEvent(
            event_type="transform.end",
            tool="dbt",
            project_dir="/app/workflows/transforms/dbt",
            target="prod",
            model_names=["stg_users", "dim_users"],
            status="success",
            metrics={"execution_time": 120.5}
        )
        ```

    """

    tool: str
    project_dir: str | None = None
    target: str | None = None
    partition_key: str | None = None
    asset_key: str | None = None
    model_names: list[str] = field(default_factory=list)
    status: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(kw_only=True)
class PublishEvent(HookEvent):
    """Event emitted when publishing data to downstream targets."""

    asset_key: str | None = None
    target_system: str | None = None
    tables: dict[str, str] = field(default_factory=dict)
    status: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(kw_only=True)
class QualityResultEvent(HookEvent):
    """Event emitted with data quality check outcomes: pass/fail status,
    severity level when failed, check type, and check-specific metadata.

    Event Types:
        - ``quality.result``: Quality check completed with results
    """

    asset_key: str
    check_name: str
    passed: bool
    severity: str | None = None
    check_type: str | None = None
    partition_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(kw_only=True)
class LineageEvent(HookEvent):
    """Event emitted for lineage edges between assets."""

    edges: list[tuple[str, str]] = field(default_factory=list)
    asset_keys: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(kw_only=True)
class TelemetryEvent(HookEvent):
    """Event emitted for telemetry metrics and logs."""

    name: str
    value: Any | None = None
    level: str | None = None
    unit: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(kw_only=True)
class SchemaMigrationEvent(HookEvent):
    """Event emitted for schema migration lifecycle stages."""

    table_name: str
    classification: str
    change_count: int
    status: str
    changes: list[dict[str, Any]] = field(default_factory=list)


@dataclass(kw_only=True)
class DataMigrationEvent(HookEvent):
    """Event emitted for data migration lifecycle stages."""

    migration_name: str
    source_type: str
    destination_table: str
    status: str
    rows_read: int
    rows_written: int
    chunk_index: int | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(kw_only=True)
class RunEvidenceObservationEvent(HookEvent):
    """Provider-neutral observation emitted after an authoritative result."""

    observation_type: str
    status: str
    run_status: str | None = None
    stage_id: str | None = None
    resources: list[dict[str, Any]] = field(default_factory=list)
    catalog_change: dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(kw_only=True)
class LogEvent(HookEvent):
    """Event emitted for structured log records."""

    logger: str
    level: str
    message: str
    service: str | None = None
    run_id: str | None = None
    asset_key: str | None = None
    job_name: str | None = None
    partition_key: str | None = None
    check_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
