"""Hook plugin that translates Phlo events into OTel spans and metrics.

This module implements the OtelHookPlugin, which integrates Phlo's hook system
with OpenTelemetry. It translates various Phlo events (ingestion, transforms,
quality checks, lineage, etc.) into OTel spans and metrics for comprehensive
observability across the data platform.

The plugin handles event correlation, trace context propagation, and metric
collection, ensuring distributed traces can follow data pipelines across
multiple services and operations.

Example:
    The plugin is automatically discovered and loaded by Phlo's plugin system:

    from phlo.plugins.hooks import HookPlugin

    # Events are emitted via Phlo's hook bus and automatically translated:
    bus.emit(IngestionEvent(...))  # Creates OTel span + metrics

"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from opentelemetry._logs import LogRecord, SeverityNumber
from opentelemetry.context import Context
from opentelemetry.trace import (
    NonRecordingSpan,
    Span,
    SpanContext,
    Status,
    StatusCode,
    TraceFlags,
    TraceState,
    get_current_span,
    set_span_in_context,
)

from phlo.hooks.events import DataMigrationEvent
from phlo.hooks.events import HookCorrelation
from phlo.hooks.events import LogEvent
from phlo.hooks.events import LineageEvent
from phlo.hooks.events import PublishEvent
from phlo.hooks.events import QualityResultEvent
from phlo.hooks.events import SchemaMigrationEvent
from phlo.hooks.events import ServiceLifecycleEvent
from phlo.hooks.events import TelemetryEvent
from phlo.hooks.events import TransformEvent
from phlo.hooks.events import IngestionEvent
from phlo.plugins.base import PluginMetadata
from phlo.plugins.hooks import HookPlugin, HookRegistration

from phlo_otel.provider import get_log_emitter, get_meter, get_tracer


class OtelHookPlugin(HookPlugin):
    """Emit OTel traces and metrics from Phlo hook events.

    Translates Phlo hook events (ingestion, transforms, quality checks,
    lineage, publish, service lifecycle, schema/data migrations, telemetry,
    and logs) into OpenTelemetry spans and metrics, correlating events and
    propagating trace context so distributed traces follow pipelines across
    services.
    """

    def __init__(self) -> None:
        """Initialize the OtelHookPlugin with empty instrument caches."""
        self._counter_cache: dict[tuple[str, str], Any] = {}
        self._gauge_cache: dict[tuple[str, str], Any] = {}
        self._histogram_cache: dict[tuple[str, str], Any] = {}
        self._up_down_counter_cache: dict[tuple[str, str], Any] = {}

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for discovery and identification."""
        return PluginMetadata(
            name="otel",
            version="0.1.0",
            description="OpenTelemetry instrumentation for Phlo",
        )

    def get_hooks(self) -> list[HookRegistration]:
        """Return registrations mapping each supported Phlo event to its handler."""
        return [
            HookRegistration(
                hook_name="otel_ingestion",
                handler=self._handle_ingestion,
            ),
            HookRegistration(
                hook_name="otel_transform",
                handler=self._handle_transform,
            ),
            HookRegistration(
                hook_name="otel_quality",
                handler=self._handle_quality,
            ),
            HookRegistration(
                hook_name="otel_lineage",
                handler=self._handle_lineage,
            ),
            HookRegistration(
                hook_name="otel_publish",
                handler=self._handle_publish,
            ),
            HookRegistration(
                hook_name="otel_service_lifecycle",
                handler=self._handle_service_lifecycle,
            ),
            HookRegistration(
                hook_name="otel_schema_migration",
                handler=self._handle_schema_migration,
            ),
            HookRegistration(
                hook_name="otel_data_migration",
                handler=self._handle_data_migration,
            ),
            HookRegistration(
                hook_name="otel_telemetry",
                handler=self._handle_telemetry,
            ),
            HookRegistration(
                hook_name="otel_log_record",
                handler=self._handle_log_record,
            ),
        ]

    def _handle_ingestion(self, event: Any) -> None:
        """Handle IngestionEvent: emit an ingestion span plus run/rows/duration metrics.

        Prefers rows_loaded over rows_processed when counting ingested rows;
        increments phlo.errors when the event reports failure.
        """
        if not isinstance(event, IngestionEvent):
            return

        correlation = self._merge_correlation(
            event.correlation,
            run_id=event.run_id,
            asset_key=event.asset_key,
            partition_key=event.partition_key,
        )
        tracer = get_tracer()
        with tracer.start_as_current_span(
            f"ingestion.{event.table_name}",
            attributes={
                "phlo.asset_key": event.asset_key,
                "phlo.stage": "ingestion",
                "phlo.table_name": event.table_name,
                "phlo.group_name": event.group_name,
                "phlo.event_type": event.event_type,
            },
            context=self._build_parent_context(correlation),
        ) as span:
            self._set_correlation_attributes(span, correlation)
            self._set_attribute_if_present(span, "phlo.system", event.tags.get("source"))
            if event.status:
                span.set_attribute("phlo.status", event.status)
            if event.error:
                span.set_status(Status(status_code=StatusCode.ERROR, description=event.error))
            if event.metrics:
                for key, value in event.metrics.items():
                    if isinstance(value, (int, float)):
                        span.set_attribute(f"phlo.metrics.{key}", value)
            self._set_event_tags(span, event.tags)
            self._record_failure(event_name="ingestion", status=event.status, error=event.error)

        counter = self._get_counter(
            "phlo.ingestion.runs",
            description="Number of ingestion runs",
        )
        counter.add(1, {"table_name": event.table_name, "status": event.status or "unknown"})

        rows = event.metrics.get("rows_loaded")
        if rows is None:
            rows = event.metrics.get("rows_processed")
        if isinstance(rows, (int, float)):
            rows_counter = self._get_counter(
                "phlo.ingestion.rows",
                description="Rows ingested",
            )
            rows_counter.add(int(rows), {"table_name": event.table_name})

        self._record_duration(
            "phlo.ingestion.duration",
            metrics=event.metrics,
            attributes={"table_name": event.table_name, "status": event.status or "unknown"},
            description="Ingestion duration",
        )

    def _handle_transform(self, event: Any) -> None:
        """Handle TransformEvent: emit a transform span plus runs counter and duration histogram."""
        if not isinstance(event, TransformEvent):
            return

        correlation = self._merge_correlation(
            event.correlation,
            run_id=getattr(event, "run_id", None),
            asset_key=event.asset_key,
            partition_key=event.partition_key,
        )
        tracer = get_tracer()
        span_name = f"transform.{event.tool}"
        if event.target:
            span_name = f"transform.{event.tool}.{event.target}"

        with tracer.start_as_current_span(
            span_name,
            attributes={
                "phlo.stage": "transform",
                "phlo.system": event.tool,
                "phlo.tool": event.tool,
                "phlo.event_type": event.event_type,
            },
            context=self._build_parent_context(correlation),
        ) as span:
            self._set_correlation_attributes(span, correlation)
            if event.target:
                span.set_attribute("phlo.target", event.target)
            if event.model_names:
                span.set_attribute("phlo.model_names", event.model_names)
            if event.status:
                span.set_attribute("phlo.status", event.status)
            if event.error:
                span.set_status(Status(status_code=StatusCode.ERROR, description=event.error))
            self._set_numeric_span_attributes(span, event.metrics)
            self._set_event_tags(span, event.tags)
            self._record_failure(event_name="transform", status=event.status, error=event.error)

        counter = self._get_counter(
            "phlo.transform.runs",
            description="Number of transform runs",
        )
        counter.add(1, {"tool": event.tool, "status": event.status or "unknown"})

        self._record_duration(
            "phlo.transform.duration",
            metrics=event.metrics,
            attributes={"tool": event.tool, "status": event.status or "unknown"},
            description="Transform duration",
        )

    def _handle_quality(self, event: Any) -> None:
        """Handle QualityResultEvent: emit a check span and pass/fail counter.

        Failed checks mark the span ERROR and increment phlo.errors.
        """
        if not isinstance(event, QualityResultEvent):
            return

        correlation = self._merge_correlation(
            event.correlation,
            run_id=getattr(event, "run_id", None),
            asset_key=event.asset_key,
            partition_key=event.partition_key,
            check_name=event.check_name,
        )
        tracer = get_tracer()
        with tracer.start_as_current_span(
            f"quality.{event.check_name}",
            attributes={
                "phlo.asset_key": event.asset_key,
                "phlo.stage": "quality",
                "phlo.check_name": event.check_name,
                "phlo.passed": event.passed,
                "phlo.event_type": event.event_type,
            },
            context=self._build_parent_context(correlation),
        ) as span:
            self._set_correlation_attributes(span, correlation)
            self._set_attribute_if_present(span, "phlo.system", event.tags.get("backend"))
            self._set_attribute_if_present(span, "phlo.operation", event.check_type)
            if event.severity:
                span.set_attribute("phlo.severity", event.severity)
            if event.check_type:
                span.set_attribute("phlo.check_type", event.check_type)
            for key, value in event.metadata.items():
                if isinstance(value, (bool, int, float, str)):
                    span.set_attribute(f"phlo.metadata.{key}", value)
            if not event.passed:
                span.set_status(
                    Status(
                        status_code=StatusCode.ERROR,
                        description=f"Quality check failed: {event.check_name}",
                    )
                )
                self._record_failure(event_name="quality", status="fail")
            self._set_event_tags(span, event.tags)

        counter = self._get_counter(
            "phlo.quality.checks",
            description="Quality check executions",
        )
        result = "pass" if event.passed else "fail"
        counter.add(1, {"check_name": event.check_name, "result": result})

    def _handle_lineage(self, event: Any) -> None:
        """Handle LineageEvent: emit an edge-count span plus lineage event/edge counters."""
        if not isinstance(event, LineageEvent):
            return

        correlation = self._merge_correlation(event.correlation)
        tracer = get_tracer()
        with tracer.start_as_current_span(
            "lineage.edges",
            attributes={
                "phlo.event_type": event.event_type,
                "phlo.stage": "lineage",
                "phlo.operation": "edges",
                "phlo.edge_count": len(event.edges),
                "phlo.asset_count": len(event.asset_keys),
            },
            context=self._build_parent_context(correlation),
        ) as span:
            self._set_correlation_attributes(span, correlation)
            if event.asset_keys:
                span.set_attribute("phlo.asset_keys", sorted(event.asset_keys))
            self._set_event_tags(span, event.tags)
            for key, value in event.metadata.items():
                if isinstance(value, (bool, int, float, str)):
                    span.set_attribute(f"phlo.metadata.{key}", value)

        event_counter = self._get_counter(
            "phlo.lineage.events",
            description="Number of lineage events",
        )
        edge_counter = self._get_counter(
            "phlo.lineage.edges",
            description="Number of lineage edges",
        )
        attributes = self._metric_attributes_from_tags(event.tags)
        event_counter.add(1, attributes)
        edge_counter.add(len(event.edges), attributes)

    def _handle_publish(self, event: Any) -> None:
        """Handle PublishEvent: emit a publish span plus run/table counters and duration."""
        if not isinstance(event, PublishEvent):
            return

        correlation = self._merge_correlation(
            event.correlation,
            run_id=getattr(event, "run_id", None),
            asset_key=event.asset_key,
            partition_key=getattr(event, "partition_key", None),
        )
        target_system = event.target_system or "unknown"
        tracer = get_tracer()
        with tracer.start_as_current_span(
            f"publish.{target_system}",
            attributes={
                "phlo.event_type": event.event_type,
                "phlo.stage": "publish",
                "phlo.system": target_system,
                "phlo.operation": "publish",
                "phlo.target_system": target_system,
            },
            context=self._build_parent_context(correlation),
        ) as span:
            self._set_correlation_attributes(span, correlation)
            if event.status:
                span.set_attribute("phlo.status", event.status)
            if event.tables:
                span.set_attribute("phlo.table_count", len(event.tables))
                span.set_attribute("phlo.tables", sorted(event.tables.values()))
            if event.error:
                span.set_status(Status(status_code=StatusCode.ERROR, description=event.error))
            self._set_numeric_span_attributes(span, event.metrics)
            self._set_event_tags(span, event.tags)
            self._record_failure(event_name="publish", status=event.status, error=event.error)

        counter = self._get_counter(
            "phlo.publish.runs",
            description="Number of publish runs",
        )
        counter.add(1, {"target_system": target_system, "status": event.status or "unknown"})

        if event.tables:
            table_counter = self._get_counter(
                "phlo.publish.tables",
                description="Number of published tables",
            )
            table_counter.add(len(event.tables), {"target_system": target_system})

        self._record_duration(
            "phlo.publish.duration",
            metrics=event.metrics,
            attributes={"target_system": target_system, "status": event.status or "unknown"},
            description="Publish duration",
        )

    def _handle_service_lifecycle(self, event: Any) -> None:
        """Handle ServiceLifecycleEvent: emit a per-phase span and lifecycle counter.

        Failed phases mark the span ERROR and increment phlo.errors.
        """
        if not isinstance(event, ServiceLifecycleEvent):
            return

        correlation = self._merge_correlation(event.correlation)
        phase = event.phase or "unknown"
        tracer = get_tracer()
        with tracer.start_as_current_span(
            f"service.{event.service_name}.{phase}",
            attributes={
                "phlo.event_type": event.event_type,
                "phlo.stage": "service",
                "phlo.system": "service",
                "phlo.operation": phase,
                "phlo.service_name": event.service_name,
                "phlo.phase": phase,
            },
            context=self._build_parent_context(correlation),
        ) as span:
            self._set_correlation_attributes(span, correlation)
            if event.project_name:
                span.set_attribute("phlo.project_name", event.project_name)
            if event.project_root:
                span.set_attribute("phlo.project_root", event.project_root)
            if event.container_name:
                span.set_attribute("phlo.container_name", event.container_name)
            if event.status:
                span.set_attribute("phlo.status", event.status)
                if self._is_failure_status(event.status):
                    span.set_status(
                        Status(
                            status_code=StatusCode.ERROR,
                            description=f"Service lifecycle failed: {phase}",
                        )
                    )
            self._set_event_tags(span, event.tags)
            self._record_failure(event_name="service_lifecycle", status=event.status)

        counter = self._get_counter(
            "phlo.service.lifecycle.events",
            description="Number of service lifecycle events",
        )
        counter.add(
            1,
            {
                "service_name": event.service_name,
                "phase": phase,
                "status": event.status or "unknown",
            },
        )

    def _handle_schema_migration(self, event: Any) -> None:
        """Handle SchemaMigrationEvent: emit a DDL span plus run/change counters.

        Failed migrations mark the span ERROR and increment phlo.errors.
        """
        if not isinstance(event, SchemaMigrationEvent):
            return

        correlation = self._merge_correlation(event.correlation)
        tracer = get_tracer()
        with tracer.start_as_current_span(
            f"schema_migration.{event.table_name}",
            attributes={
                "phlo.event_type": event.event_type,
                "phlo.stage": "migration",
                "phlo.system": "schema",
                "phlo.operation": "schema_migration",
                "phlo.table_name": event.table_name,
                "phlo.classification": event.classification,
                "phlo.change_count": event.change_count,
                "phlo.status": event.status,
            },
            context=self._build_parent_context(correlation),
        ) as span:
            self._set_correlation_attributes(span, correlation)
            if event.changes:
                span.set_attribute("phlo.schema_change_count", len(event.changes))
            self._set_event_tags(span, event.tags)
            if self._is_failure_status(event.status):
                span.set_status(
                    Status(
                        status_code=StatusCode.ERROR,
                        description=f"Schema migration failed: {event.table_name}",
                    )
                )
            self._record_failure(event_name="schema_migration", status=event.status)

        labels = {"classification": event.classification, "status": event.status}
        counter = self._get_counter(
            "phlo.schema_migration.runs",
            description="Number of schema migration events",
        )
        counter.add(1, labels)

        changes_counter = self._get_counter(
            "phlo.schema_migration.changes",
            description="Schema change count",
        )
        changes_counter.add(event.change_count, labels)

    def _handle_data_migration(self, event: Any) -> None:
        """Handle DataMigrationEvent: emit a migration span plus row counters and duration.

        Failed migrations mark the span ERROR and increment phlo.errors.
        """
        if not isinstance(event, DataMigrationEvent):
            return

        correlation = self._merge_correlation(
            event.correlation,
            run_id=getattr(event, "run_id", None),
        )
        tracer = get_tracer()
        with tracer.start_as_current_span(
            f"data_migration.{event.migration_name}",
            attributes={
                "phlo.event_type": event.event_type,
                "phlo.stage": "migration",
                "phlo.system": event.source_type,
                "phlo.operation": "data_migration",
                "phlo.migration_name": event.migration_name,
                "phlo.source_type": event.source_type,
                "phlo.destination_table": event.destination_table,
                "phlo.rows_read": event.rows_read,
                "phlo.rows_written": event.rows_written,
                "phlo.status": event.status,
            },
            context=self._build_parent_context(correlation),
        ) as span:
            self._set_correlation_attributes(span, correlation)
            if event.chunk_index is not None:
                span.set_attribute("phlo.chunk_index", event.chunk_index)
            self._set_numeric_span_attributes(span, event.metrics)
            self._set_event_tags(span, event.tags)
            if self._is_failure_status(event.status):
                span.set_status(
                    Status(
                        status_code=StatusCode.ERROR,
                        description=f"Data migration failed: {event.migration_name}",
                    )
                )
            self._record_failure(event_name="data_migration", status=event.status)

        labels = {"source_type": event.source_type, "status": event.status}
        runs_counter = self._get_counter(
            "phlo.data_migration.runs",
            description="Number of data migration events",
        )
        runs_counter.add(1, labels)

        rows_read_counter = self._get_counter(
            "phlo.data_migration.rows_read",
            description="Rows read during data migration",
        )
        rows_read_counter.add(event.rows_read, labels)

        rows_written_counter = self._get_counter(
            "phlo.data_migration.rows_written",
            description="Rows written during data migration",
        )
        rows_written_counter.add(event.rows_written, labels)

        self._record_duration(
            "phlo.data_migration.duration",
            metrics=event.metrics,
            attributes=labels,
            description="Data migration duration",
        )

    def _handle_telemetry(self, event: Any) -> None:
        """Route a TelemetryEvent to the cached instrument matching its metric_kind.

        Supports counter, gauge, histogram, and up_down_counter kinds.
        Non-numeric values are silently ignored; iceberg.maintenance.* names
        are promoted to standard phlo.maintenance.* metrics.
        """
        if not isinstance(event, TelemetryEvent):
            return
        if event.event_type != "telemetry.metric":
            return
        if not isinstance(event.value, (int, float)):
            return
        if self._handle_maintenance_telemetry(event):
            return

        metric_name = f"phlo.telemetry.{event.name}"
        metric_kind = self._resolve_metric_kind(event.payload)
        attributes = self._normalize_metric_attributes(event.payload)
        unit = event.unit or ""

        if metric_kind == "counter":
            counter = self._get_counter(
                metric_name,
                description=f"Telemetry metric: {event.name}",
                unit=unit,
            )
            counter.add(event.value, attributes)
            return

        if metric_kind == "histogram":
            histogram = self._get_histogram(
                metric_name,
                unit=unit,
                description=f"Telemetry metric: {event.name}",
            )
            histogram.record(event.value, attributes)
            return

        if metric_kind == "up_down_counter":
            counter = self._get_up_down_counter(
                metric_name,
                unit=unit,
                description=f"Telemetry metric: {event.name}",
            )
            counter.add(event.value, attributes)
            return

        gauge = self._get_gauge(
            metric_name,
            unit=unit,
            description=f"Telemetry metric: {event.name}",
        )
        gauge.set(event.value, attributes)

    def _handle_maintenance_telemetry(self, event: TelemetryEvent) -> bool:
        """Map iceberg.maintenance.* telemetry to phlo.maintenance.* metrics.

        Returns True when the event was a recognized maintenance metric.
        """
        if not event.name.startswith("iceberg.maintenance."):
            return False

        attributes = {
            **self._metric_attributes_from_tags(event.tags),
            **self._normalize_metric_attributes(event.payload),
        }
        metric_map: dict[str, tuple[str, str]] = {
            "iceberg.maintenance.run": ("counter", "phlo.maintenance.runs"),
            "iceberg.maintenance.duration_seconds": ("histogram", "phlo.maintenance.duration"),
            "iceberg.maintenance.tables_processed": (
                "counter",
                "phlo.maintenance.tables_processed",
            ),
            "iceberg.maintenance.errors": ("counter", "phlo.maintenance.errors"),
            "iceberg.maintenance.snapshots_deleted": (
                "counter",
                "phlo.maintenance.snapshots_deleted",
            ),
            "iceberg.maintenance.orphan_files": ("counter", "phlo.maintenance.orphan_files"),
            "iceberg.maintenance.total_records": ("counter", "phlo.maintenance.records_processed"),
            "iceberg.maintenance.total_size_mb": ("histogram", "phlo.maintenance.size_mb"),
        }
        mapping = metric_map.get(event.name)
        if mapping is None:
            return False

        metric_type, metric_name = mapping
        if metric_type == "counter":
            counter = self._get_counter(
                metric_name,
                description=f"Maintenance metric derived from {event.name}",
                unit=event.unit or "",
            )
            counter.add(event.value, attributes)
            return True

        histogram = self._get_histogram(
            metric_name,
            description=f"Maintenance metric derived from {event.name}",
            unit=event.unit or "",
        )
        histogram.record(event.value, attributes)
        return True

    def _handle_log_record(self, event: Any) -> None:
        """Convert a LogEvent to an OTel LogRecord and emit it.

        Preserves trace context from correlation or derives stable ids from
        run_id. Silently drops the record when log export is disabled.
        """
        if not isinstance(event, LogEvent):
            return

        attributes = self._build_log_attributes(event)
        trace_id, span_id, trace_flags = self._resolve_log_context(event)
        log_record = LogRecord(
            timestamp=self._datetime_to_unix_nanos(event.timestamp),
            observed_timestamp=self._datetime_to_unix_nanos(datetime.now(event.timestamp.tzinfo)),
            trace_id=trace_id,
            span_id=span_id,
            trace_flags=trace_flags,
            severity_text=event.level.upper(),
            severity_number=self._map_severity(event.level),
            body=event.message,
            attributes=attributes,
        )
        emitter = get_log_emitter()
        if emitter is None:
            return
        emitter.emit(log_record)

    def _get_counter(self, name: str, *, description: str, unit: str = "") -> Any:
        """Return the cached counter for name and unit, creating it on first use."""
        cache_key = (name, unit)
        counter = self._counter_cache.get(cache_key)
        if counter is None:
            counter = get_meter().create_counter(name, unit=unit, description=description)
            self._counter_cache[cache_key] = counter
        return counter

    def _get_gauge(self, name: str, *, unit: str, description: str) -> Any:
        """Return the cached gauge for name and unit, creating it on first use."""
        cache_key = (name, unit)
        gauge = self._gauge_cache.get(cache_key)
        if gauge is None:
            gauge = get_meter().create_gauge(name, unit=unit, description=description)
            self._gauge_cache[cache_key] = gauge
        return gauge

    def _get_histogram(self, name: str, *, unit: str, description: str) -> Any:
        """Return the cached histogram for name and unit, creating it on first use."""
        cache_key = (name, unit)
        histogram = self._histogram_cache.get(cache_key)
        if histogram is None:
            histogram = get_meter().create_histogram(name, unit=unit, description=description)
            self._histogram_cache[cache_key] = histogram
        return histogram

    def _get_up_down_counter(self, name: str, *, unit: str, description: str) -> Any:
        """Return the cached up-down counter for name and unit, creating it on first use."""
        cache_key = (name, unit)
        counter = self._up_down_counter_cache.get(cache_key)
        if counter is None:
            counter = get_meter().create_up_down_counter(name, unit=unit, description=description)
            self._up_down_counter_cache[cache_key] = counter
        return counter

    def _resolve_metric_kind(self, payload: dict[str, Any]) -> str:
        """Resolve the metric kind from reserved payload attributes.

        Defaults to "gauge" when absent, invalid, or unrecognized.
        """
        raw_kind = payload.get("metric_kind", payload.get("otel_metric_kind", "gauge"))
        if not isinstance(raw_kind, str):
            return "gauge"

        metric_kind = raw_kind.strip().lower().replace("-", "_")
        valid_kinds = {"counter", "gauge", "histogram", "up_down_counter"}
        if metric_kind not in valid_kinds:
            return "gauge"
        return metric_kind

    def _normalize_metric_attributes(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Filter payload keys to allowed metric labels and coerce their values."""
        normalized: dict[str, Any] = {}
        for key, value in payload.items():
            if key not in self._allowed_metric_label_keys():
                continue
            normalized[key] = self._coerce_metric_attribute_value(value)
        return normalized

    def _set_numeric_span_attributes(self, span: Span, metrics: dict[str, Any]) -> None:
        """Set numeric entries of metrics on the span as phlo.metrics.<key> attributes."""
        for key, value in metrics.items():
            if isinstance(value, (int, float)):
                span.set_attribute(f"phlo.metrics.{key}", value)

    def _set_event_tags(self, span: Span, tags: dict[str, str]) -> None:
        """Copy event tags onto the span as phlo.tags.<key> attributes."""
        for key, value in tags.items():
            span.set_attribute(f"phlo.tags.{key}", value)

    def _set_correlation_attributes(self, span: Span, correlation: HookCorrelation) -> None:
        """Copy correlation identifiers onto the span as phlo.* attributes."""
        if correlation.request_id:
            span.set_attribute("phlo.request_id", correlation.request_id)
        if correlation.run_id:
            span.set_attribute("phlo.run_id", correlation.run_id)
        if correlation.asset_key:
            span.set_attribute("phlo.asset_key", correlation.asset_key)
        if correlation.job_name:
            span.set_attribute("phlo.job_name", correlation.job_name)
        if correlation.partition_key:
            span.set_attribute("phlo.partition_key", correlation.partition_key)
        if correlation.check_name:
            span.set_attribute("phlo.check_name", correlation.check_name)

    def _set_attribute_if_present(self, span: Span, key: str, value: str | None) -> None:
        """Set the span attribute only when value is truthy."""
        if value:
            span.set_attribute(key, value)

    def _metric_attributes_from_tags(self, tags: dict[str, str]) -> dict[str, str]:
        """Filter tags down to the allowed metric label keys."""
        return {
            key: value for key, value in tags.items() if key in self._allowed_metric_label_keys()
        }

    def _allowed_metric_label_keys(self) -> set[str]:
        """Return the fixed set of allowed metric label keys.

        Kept closed to prevent high-cardinality metric dimensions while still
        allowing useful filtering.
        """
        return {
            "backend",
            "classification",
            "environment",
            "namespace",
            "operation",
            "phase",
            "result",
            "service",
            "source",
            "source_type",
            "status",
            "target",
            "target_system",
            "tool",
        }

    def _build_log_attributes(self, event: LogEvent) -> dict[str, Any]:
        """Build OTel log attributes from a LogEvent.

        Includes stage/logger/service fields, merged correlation identifiers,
        event tags, and coerced metadata.
        """
        correlation = self._merge_correlation(
            event.correlation,
            run_id=event.run_id,
            asset_key=event.asset_key,
            job_name=event.job_name,
            partition_key=event.partition_key,
            check_name=event.check_name,
        )
        attributes: dict[str, Any] = {
            "phlo.event_type": event.event_type,
            "phlo.stage": self._event_stage(event.event_type),
            "phlo.logger": event.logger,
            "phlo.level": event.level,
        }
        if event.service:
            attributes["phlo.service"] = event.service
            attributes["phlo.system"] = event.service
        operation = event.tags.get("operation")
        if operation:
            attributes["phlo.operation"] = operation
        if correlation.request_id:
            attributes["phlo.request_id"] = correlation.request_id
        if correlation.run_id:
            attributes["phlo.run_id"] = correlation.run_id
        if correlation.asset_key:
            attributes["phlo.asset_key"] = correlation.asset_key
        if correlation.job_name:
            attributes["phlo.job_name"] = correlation.job_name
        if correlation.partition_key:
            attributes["phlo.partition_key"] = correlation.partition_key
        if correlation.check_name:
            attributes["phlo.check_name"] = correlation.check_name

        for key, value in event.tags.items():
            attributes[f"phlo.tag.{key}"] = value
        for key, value in event.metadata.items():
            attributes[f"phlo.metadata.{key}"] = self._coerce_attribute_value(value)
        return attributes

    def _coerce_attribute_value(self, value: Any) -> Any:
        """Coerce a value to a valid OTel attribute type.

        OTel accepts primitives and homogeneous primitive lists; anything else
        falls back to its string form.
        """
        if isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, (list, tuple)):
            return [
                item if isinstance(item, (bool, int, float, str)) else str(item) for item in value
            ]
        return str(value)

    def _coerce_metric_attribute_value(self, value: Any) -> Any:
        """Coerce a value to a valid metric attribute type.

        Like _coerce_attribute_value but returns tuples so list values stay
        immutable as metric dimensions.
        """
        if isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, (list, tuple)):
            return tuple(
                item if isinstance(item, (bool, int, float, str)) else str(item) for item in value
            )
        return str(value)

    def _map_severity(self, level: str) -> SeverityNumber:
        """Map a log level string to a SeverityNumber, defaulting to INFO."""
        normalized = level.strip().lower()
        if normalized == "debug":
            return SeverityNumber.DEBUG
        if normalized in {"warning", "warn"}:
            return SeverityNumber.WARN
        if normalized == "error":
            return SeverityNumber.ERROR
        if normalized == "critical":
            return SeverityNumber.FATAL
        return SeverityNumber.INFO

    def _event_stage(self, event_type: str) -> str:
        """Derive the pipeline stage from an event type prefix."""
        prefix = event_type.split(".", maxsplit=1)[0]
        if prefix in {"schema_migration", "data_migration"}:
            return "migration"
        if prefix == "service":
            return "service"
        if prefix == "telemetry":
            return "telemetry"
        if prefix == "log":
            return "log"
        return prefix

    def _is_failure_status(self, status: str) -> bool:
        """Return True when the status string indicates failure."""
        return status.lower() in {"error", "failed", "failure", "rejected"}

    def _record_duration(
        self,
        metric_name: str,
        *,
        metrics: dict[str, Any],
        attributes: dict[str, str],
        description: str,
    ) -> None:
        """Record a duration histogram when the metrics dict carries a duration."""
        duration = self._extract_duration_seconds(metrics)
        if duration is None:
            return

        histogram = self._get_histogram(metric_name, unit="s", description=description)
        histogram.record(duration, attributes)

    def _extract_duration_seconds(self, metrics: dict[str, Any]) -> float | None:
        """Return the first recognized duration value in seconds, or None."""
        for key in ("duration_seconds", "total_elapsed_seconds", "elapsed_seconds"):
            value = metrics.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        return None

    def _record_failure(
        self,
        *,
        event_name: str,
        status: str | None,
        error: str | None = None,
    ) -> None:
        """Increment phlo.errors when the status or error message signals failure."""
        if not (error or (status and self._is_failure_status(status))):
            return

        counter = self._get_counter(
            "phlo.errors",
            description="Number of failed Phlo workflow events",
        )
        counter.add(1, {"event": event_name, "status": status or "error"})

    def _datetime_to_unix_nanos(self, value: datetime) -> int:
        """Convert a datetime to a Unix timestamp in nanoseconds."""
        return int(value.timestamp() * 1_000_000_000)

    def _resolve_log_context(self, event: LogEvent) -> tuple[int | None, int | None, Any | None]:
        """Resolve (trace_id, span_id, trace_flags) for a log event.

        Prefers explicit correlation/metadata context, then the active span,
        then stable identifiers derived from run_id/request_id. Values may be
        None when no context can be determined.
        """
        correlation = self._merge_correlation(
            event.correlation,
            trace_id=event.metadata.get("trace_id"),
            span_id=event.metadata.get("span_id"),
            trace_flags=event.metadata.get("trace_flags"),
            run_id=event.run_id,
            asset_key=event.asset_key,
            job_name=event.job_name,
            partition_key=event.partition_key,
            check_name=event.check_name,
        )
        span_context = get_current_span().get_span_context()
        trace_id = span_id = trace_flags = None
        if span_context.is_valid:
            trace_id = span_context.trace_id
            span_id = span_context.span_id
            trace_flags = span_context.trace_flags

        metadata_trace_id = self._parse_trace_identifier(correlation.trace_id)
        metadata_span_id = self._parse_trace_identifier(correlation.span_id)
        metadata_trace_flags = self._parse_trace_flags(correlation.trace_flags)
        if metadata_trace_id is not None:
            trace_id = metadata_trace_id
        if metadata_span_id is not None:
            span_id = metadata_span_id
        if metadata_trace_flags is not None:
            trace_flags = metadata_trace_flags
        if trace_id is None or span_id is None:
            synthetic_trace_id, synthetic_span_id = self._derive_trace_context_identifiers(
                correlation
            )
            if trace_id is None:
                trace_id = synthetic_trace_id
            if span_id is None:
                span_id = synthetic_span_id
            if trace_flags is None and synthetic_trace_id is not None:
                trace_flags = TraceFlags(0x01)
        return trace_id, span_id, trace_flags

    def _merge_correlation(self, correlation: HookCorrelation, **overrides: Any) -> HookCorrelation:
        """Return a copy of correlation with non-None keyword overrides applied."""
        merged = HookCorrelation(**vars(correlation))
        for key, value in overrides.items():
            if value is not None:
                setattr(merged, key, str(value))
        return merged

    def _build_parent_context(self, correlation: HookCorrelation) -> Context | None:
        """Build a remote-parent OTel context from correlation trace ids.

        Falls back to stable synthetic ids derived from run_id/request_id so
        traces remain continuous without explicit propagation; returns None
        when no context can be constructed.
        """
        trace_id = self._parse_trace_identifier(correlation.trace_id)
        span_id = self._parse_trace_identifier(correlation.span_id)
        if trace_id is None or span_id is None:
            synthetic_trace_id, synthetic_span_id = self._derive_trace_context_identifiers(
                correlation
            )
            if trace_id is None:
                trace_id = synthetic_trace_id
            if span_id is None:
                span_id = synthetic_span_id
        if trace_id is None or span_id is None:
            return None

        trace_flags = self._parse_trace_flags(correlation.trace_flags) or TraceFlags(0x01)
        span_context = SpanContext(
            trace_id=trace_id,
            span_id=span_id,
            is_remote=True,
            trace_flags=trace_flags,
            trace_state=TraceState(),
        )
        return set_span_in_context(NonRecordingSpan(span_context))

    def _derive_trace_context_identifiers(
        self,
        correlation: HookCorrelation,
    ) -> tuple[int | None, int | None]:
        """Derive deterministic (trace_id, span_id) from run_id/request_id.

        Enables trace continuity across process boundaries without explicit
        context propagation; returns (None, None) without a grouping key.
        """
        grouping_key = self._trace_grouping_key(correlation)
        if grouping_key is None:
            return None, None

        trace_id = self._stable_identifier(f"trace:{grouping_key}", bytes_length=16)
        span_id = self._stable_identifier(f"span:{grouping_key}", bytes_length=8)
        return trace_id, span_id

    def _trace_grouping_key(self, correlation: HookCorrelation) -> str | None:
        """Return the run_id- or request_id-based grouping key, or None if neither is set."""
        if correlation.run_id:
            return f"run:{correlation.run_id}"
        if correlation.request_id:
            return f"request:{correlation.request_id}"
        return None

    def _stable_identifier(self, value: str, *, bytes_length: int) -> int:
        """Hash a string to a deterministic bytes_length-byte integer identifier."""
        digest = hashlib.sha256(value.encode("utf-8")).digest()
        return int.from_bytes(digest[:bytes_length], byteorder="big", signed=False)

    def _parse_trace_identifier(self, value: Any) -> int | None:
        """Parse a hex-string or int trace identifier, or None when invalid."""
        if value is None:
            return None
        if isinstance(value, int):
            return value
        if not isinstance(value, str):
            return None

        raw = value.strip().lower()
        if not raw:
            return None
        try:
            if raw.startswith("0x"):
                return int(raw, 16)
            return int(raw, 16)
        except ValueError:
            return None

    def _parse_trace_flags(self, value: Any) -> TraceFlags | None:
        """Parse trace flags from a string or int, or None when invalid."""
        trace_flags = self._parse_trace_identifier(value)
        if trace_flags is None:
            return None
        return TraceFlags(trace_flags & 0xFF)
