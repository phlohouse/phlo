"""Helper emitters for publishing hook events.

One context dataclass and emitter per event family: ingestion, transform,
publish, quality results, lineage, telemetry, service lifecycle, and schema
and data migrations. Correlation fields merge with the bound logging context
first, then explicit base, then per-event overrides; only non-None wins.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from phlo.hooks.bus import HookBus, get_hook_bus
from phlo.hooks.events import (
    DataMigrationEvent,
    HookCorrelation,
    IngestionEvent,
    LineageEvent,
    PublishEvent,
    QualityResultEvent,
    SchemaMigrationEvent,
    ServiceLifecycleEvent,
    TelemetryEvent,
    TransformEvent,
    normalize_attempt,
)
from phlo.logging import get_bound_correlation_context


def _merge_correlation(
    *,
    base: HookCorrelation | None = None,
    overrides: dict[str, Any] | None = None,
) -> HookCorrelation:
    """Merge correlation fields from three sources, lowest precedence first:
    the bound context, the explicit base, then per-event overrides. A source
    only replaces a field with a non-None value, so unset fields fall through.
    """
    correlation = HookCorrelation(**vars(get_bound_correlation_context()))
    if base is not None:
        for key, value in vars(base).items():
            if value is not None:
                setattr(correlation, key, _normalize_correlation_value(key, value))
    if overrides is not None:
        for key, value in overrides.items():
            if value is not None:
                setattr(correlation, key, _normalize_correlation_value(key, value))
    return correlation


def _normalize_correlation_value(key: str, value: Any) -> Any:
    if key == "attempt":
        return normalize_attempt(value)
    return str(value)


class _ContextEmitterBase:
    """Shared initializer for context-based event emitters."""

    def __init__(self, context, hook_bus: HookBus | None = None) -> None:
        self._context = context
        self._hook_bus = hook_bus or get_hook_bus()

    def _emit_event(
        self,
        event: IngestionEvent
        | TransformEvent
        | PublishEvent
        | QualityResultEvent
        | LineageEvent
        | TelemetryEvent
        | ServiceLifecycleEvent
        | SchemaMigrationEvent
        | DataMigrationEvent,
        *,
        correlation_overrides: dict[str, Any] | None = None,
    ) -> None:
        """Emit a hook event with merged correlation."""
        event.correlation = _merge_correlation(
            base=self._context.correlation,
            overrides=correlation_overrides,
        )
        self._hook_bus.emit(event)

    def _event_id(self, event_type: str, explicit: str | None, *identity_parts: str) -> str:
        """Return an explicit or deterministic identity for retryable events."""
        if explicit:
            return explicit
        # Derive a stable identity from the run coordinates so retries of the
        # same work produce the same event_id and downstream consumers can
        # deduplicate. Tags are excluded from the hash: they annotate events
        # but are not part of their identity.
        context = asdict(self._context)
        context.pop("tags", None)
        context.pop("correlation", None)
        correlation = asdict(self._context.correlation)
        correlation = {
            key: correlation.get(key)
            for key in (
                "project_id",
                "run_id",
                "attempt",
                "asset_key",
                "partition_key",
                "job_name",
            )
        }
        identity = json.dumps(
            {
                "event_type": event_type,
                "context": context,
                "correlation": correlation,
                "identity_parts": identity_parts,
            },
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class IngestionEventContext:
    """Shared context for ingestion event emissions."""

    asset_key: str
    table_name: str
    group_name: str
    partition_key: str | None = None
    project_id: str | None = None
    run_id: str | None = None
    branch_name: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    correlation: HookCorrelation = field(default_factory=HookCorrelation)
    producer: str = "phlo"


class IngestionEventEmitter(_ContextEmitterBase):
    """Emit ingestion lifecycle events with a shared context."""

    def emit_start(self, *, status: str = "started", event_id: str | None = None) -> None:
        """Emit an ingestion start event."""
        self._emit(
            event_type="ingestion.start", status=status, metrics=None, error=None, event_id=event_id
        )

    def emit_end(
        self,
        *,
        status: str,
        metrics: dict[str, Any] | None = None,
        error: str | None = None,
        event_id: str | None = None,
    ) -> None:
        """Emit an ingestion end event."""
        self._emit(
            event_type="ingestion.end",
            status=status,
            metrics=metrics,
            error=error,
            event_id=event_id,
        )

    def _emit(
        self,
        *,
        event_type: str,
        status: str | None,
        metrics: dict[str, Any] | None,
        error: str | None,
        event_id: str | None,
    ) -> None:
        """Emit an ingestion event."""
        self._emit_event(
            IngestionEvent(
                event_type=event_type,
                event_id=self._event_id(event_type, event_id),
                producer=self._context.producer,
                asset_key=self._context.asset_key,
                table_name=self._context.table_name,
                group_name=self._context.group_name,
                partition_key=self._context.partition_key,
                run_id=self._context.run_id,
                branch_name=self._context.branch_name,
                status=status,
                metrics=metrics or {},
                error=error,
                tags=self._context.tags.copy(),
            ),
            correlation_overrides={
                "run_id": self._context.run_id,
                "project_id": self._context.project_id,
                "asset_key": self._context.asset_key,
                "partition_key": self._context.partition_key,
            },
        )


@dataclass(frozen=True)
class TransformEventContext:
    """Shared context for transform event emissions."""

    tool: str
    project_dir: str | None = None
    target: str | None = None
    partition_key: str | None = None
    asset_key: str | None = None
    project_id: str | None = None
    run_id: str | None = None
    model_names: list[str] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)
    correlation: HookCorrelation = field(default_factory=HookCorrelation)
    producer: str = "phlo"


class TransformEventEmitter(_ContextEmitterBase):
    """Emit transform lifecycle events with a shared context."""

    def emit_start(self, *, status: str = "started", event_id: str | None = None) -> None:
        """Emit a transform start event."""
        self._emit(
            event_type="transform.start", status=status, metrics=None, error=None, event_id=event_id
        )

    def emit_end(
        self,
        *,
        status: str,
        metrics: dict[str, Any] | None = None,
        error: str | None = None,
        event_id: str | None = None,
    ) -> None:
        """Emit a transform end event."""
        self._emit(
            event_type="transform.end",
            status=status,
            metrics=metrics,
            error=error,
            event_id=event_id,
        )

    def _emit(
        self,
        *,
        event_type: str,
        status: str | None,
        metrics: dict[str, Any] | None,
        error: str | None,
        event_id: str | None,
    ) -> None:
        """Emit a transform event."""
        self._emit_event(
            TransformEvent(
                event_type=event_type,
                event_id=self._event_id(event_type, event_id),
                producer=self._context.producer,
                tool=self._context.tool,
                project_dir=self._context.project_dir,
                target=self._context.target,
                partition_key=self._context.partition_key,
                asset_key=self._context.asset_key,
                model_names=list(self._context.model_names),
                status=status,
                metrics=metrics or {},
                error=error,
                tags=self._context.tags.copy(),
            ),
            correlation_overrides={
                "run_id": self._context.run_id,
                "project_id": self._context.project_id,
                "asset_key": self._context.asset_key,
                "partition_key": self._context.partition_key,
            },
        )


@dataclass(frozen=True)
class PublishEventContext:
    """Shared context for publish event emissions."""

    asset_key: str | None = None
    run_id: str | None = None
    project_id: str | None = None
    partition_key: str | None = None
    target_system: str | None = None
    tables: dict[str, str] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)
    correlation: HookCorrelation = field(default_factory=HookCorrelation)
    producer: str = "phlo"


class PublishEventEmitter(_ContextEmitterBase):
    """Emit publish lifecycle events with a shared context."""

    def emit_start(self, *, status: str = "started", event_id: str | None = None) -> None:
        """Emit a publish start event."""
        self._emit(
            event_type="publish.start", status=status, metrics=None, error=None, event_id=event_id
        )

    def emit_end(
        self,
        *,
        status: str,
        metrics: dict[str, Any] | None = None,
        error: str | None = None,
        event_id: str | None = None,
    ) -> None:
        """Emit a publish end event."""
        self._emit(
            event_type="publish.end", status=status, metrics=metrics, error=error, event_id=event_id
        )

    def _emit(
        self,
        *,
        event_type: str,
        status: str | None,
        metrics: dict[str, Any] | None,
        error: str | None,
        event_id: str | None,
    ) -> None:
        """Emit a publish event."""
        self._emit_event(
            PublishEvent(
                event_type=event_type,
                event_id=self._event_id(event_type, event_id),
                producer=self._context.producer,
                asset_key=self._context.asset_key,
                target_system=self._context.target_system,
                tables=self._context.tables.copy(),
                status=status,
                metrics=metrics or {},
                error=error,
                tags=self._context.tags.copy(),
            ),
            correlation_overrides={
                "run_id": self._context.run_id,
                "project_id": self._context.project_id,
                "asset_key": self._context.asset_key,
                "partition_key": self._context.partition_key,
            },
        )


@dataclass(frozen=True)
class QualityResultEventContext:
    """Shared context for quality result event emissions."""

    asset_key: str
    project_id: str | None = None
    run_id: str | None = None
    partition_key: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    correlation: HookCorrelation = field(default_factory=HookCorrelation)
    producer: str = "phlo"


class QualityResultEventEmitter(_ContextEmitterBase):
    """Emit quality result events with a shared context."""

    def emit_result(
        self,
        *,
        check_name: str,
        passed: bool,
        severity: str | None = None,
        check_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> None:
        """Emit a quality result event."""
        self._emit_event(
            QualityResultEvent(
                event_type="quality.result",
                event_id=self._event_id("quality.result", event_id, check_name),
                producer=self._context.producer,
                asset_key=self._context.asset_key,
                check_name=check_name,
                passed=passed,
                severity=severity,
                check_type=check_type,
                partition_key=self._context.partition_key,
                metadata=metadata or {},
                tags=self._context.tags.copy(),
            ),
            correlation_overrides={
                "run_id": self._context.run_id,
                "project_id": self._context.project_id,
                "asset_key": self._context.asset_key,
                "partition_key": self._context.partition_key,
                "check_name": check_name,
            },
        )


@dataclass(frozen=True)
class LineageEventContext:
    """Shared context for lineage event emissions."""

    project_id: str | None = None
    run_id: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    correlation: HookCorrelation = field(default_factory=HookCorrelation)
    producer: str = "phlo"
    operation_id: str | None = None


class LineageEventEmitter(_ContextEmitterBase):
    """Emit lineage events with a shared context."""

    def emit_edges(
        self,
        *,
        edges: list[tuple[str, str]],
        asset_keys: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        event_id: str | None = None,
        operation_id: str | None = None,
    ) -> None:
        """Emit a lineage edges event."""
        logical_operation_id = operation_id or self._context.operation_id or event_id
        # Lineage edges carry no run coordinates of their own, so an operation
        # id is required whenever emission happens inside a tracked run;
        # without one each retry would mint a distinct event_id for the same
        # edges.
        if logical_operation_id is None and self._context.correlation.run_id is not None:
            raise ValueError("lineage event requires operation_id or event_id for retry identity")
        logical_operation_id = logical_operation_id or "uncorrelated"
        self._emit_event(
            LineageEvent(
                event_type="lineage.edges",
                event_id=self._event_id("lineage.edges", event_id, logical_operation_id),
                producer=self._context.producer,
                edges=list(edges),
                asset_keys=list(asset_keys) if asset_keys else [],
                metadata=metadata or {},
                tags=self._context.tags.copy(),
            ),
            correlation_overrides={
                "project_id": self._context.project_id,
                "run_id": self._context.run_id,
            },
        )


@dataclass(frozen=True)
class TelemetryEventContext:
    """Shared context for telemetry event emissions."""

    tags: dict[str, str] = field(default_factory=dict)
    correlation: HookCorrelation = field(default_factory=HookCorrelation)


class TelemetryEventEmitter(_ContextEmitterBase):
    """Emit telemetry events with a shared context."""

    def emit_metric(
        self,
        *,
        name: str,
        value: Any | None = None,
        unit: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Emit a telemetry metric event."""
        self._emit(
            event_type="telemetry.metric",
            name=name,
            value=value,
            level=None,
            unit=unit,
            payload=payload,
        )

    def emit_log(
        self,
        *,
        name: str,
        level: str,
        value: Any | None = None,
        unit: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Emit a telemetry log event."""
        self._emit(
            event_type="telemetry.log",
            name=name,
            value=value,
            level=level,
            unit=unit,
            payload=payload,
        )

    def _emit(
        self,
        *,
        event_type: str,
        name: str,
        value: Any | None,
        level: str | None,
        unit: str | None,
        payload: dict[str, Any] | None,
    ) -> None:
        """Emit a telemetry event."""
        self._emit_event(
            TelemetryEvent(
                event_type=event_type,
                name=name,
                value=value,
                level=level,
                unit=unit,
                payload=payload or {},
                tags=self._context.tags.copy(),
            ),
        )


@dataclass(frozen=True)
class ServiceLifecycleEventContext:
    """Shared context for service lifecycle event emissions."""

    service_name: str
    project_name: str | None = None
    project_root: str | None = None
    container_name: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    correlation: HookCorrelation = field(default_factory=HookCorrelation)


class ServiceLifecycleEventEmitter(_ContextEmitterBase):
    """Emit service lifecycle events with a shared context."""

    def emit(
        self,
        *,
        phase: str,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Emit a service lifecycle event for the given phase."""
        tags = self._context.tags.copy()
        tags["service"] = self._context.service_name
        tags["phase"] = phase
        self._emit_event(
            ServiceLifecycleEvent(
                event_type=f"service.{phase}",
                service_name=self._context.service_name,
                project_name=self._context.project_name,
                project_root=self._context.project_root,
                container_name=self._context.container_name,
                phase=phase,
                status=status,
                metadata=metadata or {},
                tags=tags,
            ),
        )


@dataclass(frozen=True)
class SchemaMigrationEventContext:
    """Shared context for schema migration event emissions."""

    table_name: str
    tags: dict[str, str] = field(default_factory=dict)
    correlation: HookCorrelation = field(default_factory=HookCorrelation)


class SchemaMigrationEventEmitter(_ContextEmitterBase):
    """Emit schema migration lifecycle events with a shared context."""

    def emit(
        self,
        *,
        status: str,
        classification: str,
        change_count: int,
        changes: list[dict[str, Any]] | None = None,
    ) -> None:
        """Emit a schema migration event.

        ``status`` is one of planned, approved, applied, or rejected and
        ``classification`` carries the worst classification across changes.

        """
        self._emit_event(
            SchemaMigrationEvent(
                event_type=f"schema_migration.{status}",
                table_name=self._context.table_name,
                classification=classification,
                change_count=change_count,
                status=status,
                changes=changes or [],
                tags=self._context.tags.copy(),
            ),
        )


@dataclass(frozen=True)
class DataMigrationEventContext:
    """Shared context for data migration event emissions."""

    migration_name: str
    source_type: str
    destination_table: str
    run_id: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    correlation: HookCorrelation = field(default_factory=HookCorrelation)


class DataMigrationEventEmitter(_ContextEmitterBase):
    """Emit data migration lifecycle events with a shared context."""

    def emit(
        self,
        *,
        status: str,
        rows_read: int,
        rows_written: int,
        chunk_index: int | None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        """Emit a data migration event."""
        tags = self._context.tags.copy()
        tags["source_type"] = self._context.source_type
        tags["destination_table"] = self._context.destination_table
        self._emit_event(
            DataMigrationEvent(
                event_type=f"data_migration.{status}",
                migration_name=self._context.migration_name,
                source_type=self._context.source_type,
                destination_table=self._context.destination_table,
                status=status,
                rows_read=rows_read,
                rows_written=rows_written,
                chunk_index=chunk_index,
                metrics=metrics or {},
                tags=tags,
            ),
            correlation_overrides={"run_id": self._context.run_id},
        )
