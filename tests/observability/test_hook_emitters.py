"""Tests for hook emitters.

Every event emitter must merge correlation from its explicit context
with the log-bound context, with context-supplied values winning, and
must preserve a non-default integer attempt rather than letting a bound
default overwrite it. Covers all emitter kinds uniformly.
"""

from __future__ import annotations

import pytest

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
from phlo.hooks.events import HookCorrelation
from phlo.logging import bind_context, clear_context
from tests.helpers import RecordingBus

pytestmark = pytest.mark.core_regression


def test_ingestion_emitter_merges_bound_correlation_context() -> None:
    bus = RecordingBus()
    emitter = IngestionEventEmitter(
        IngestionEventContext(
            asset_key="silver.orders",
            table_name="orders",
            group_name="sales",
            run_id="run-123",
            partition_key="2026-03-08",
        ),
        hook_bus=bus,
    )

    bind_context(trace_id="abc123", span_id="def456", job_name="daily_orders")
    try:
        emitter.emit_end(status="success", metrics={"rows_loaded": 10})
    finally:
        clear_context()

    event = bus.events[0]
    assert event.correlation.run_id == "run-123"
    assert event.correlation.asset_key == "silver.orders"
    assert event.correlation.partition_key == "2026-03-08"
    assert event.correlation.job_name == "daily_orders"
    assert event.correlation.trace_id == "abc123"
    assert event.correlation.span_id == "def456"


def test_emitter_preserves_integer_attempt_after_correlation_merge() -> None:
    bus = RecordingBus()
    emitter = IngestionEventEmitter(
        IngestionEventContext(
            asset_key="silver.orders",
            table_name="orders",
            group_name="sales",
            correlation=HookCorrelation(run_id="run-123", attempt=2),
        ),
        hook_bus=bus,
    )

    emitter.emit_start()

    assert bus.events[0].correlation.attempt == 2
    assert isinstance(bus.events[0].correlation.attempt, int)
    assert HookCorrelation(attempt="2").attempt == 2
    with pytest.raises(ValueError, match="positive integer"):
        HookCorrelation(attempt=0)


def test_transform_emitter_merges_bound_correlation_context() -> None:
    bus = RecordingBus()
    emitter = TransformEventEmitter(
        TransformEventContext(
            tool="dbt",
            project_dir="/tmp/dbt",
            target="dev",
            asset_key="gold.customers",
            run_id="run-t1",
            partition_key="2026-03-08",
        ),
        hook_bus=bus,
    )

    bind_context(trace_id="t1", span_id="s1", job_name="transform_job")
    try:
        emitter.emit_end(status="success", metrics={"models_run": 3})
    finally:
        clear_context()

    event = bus.events[0]
    assert event.event_type == "transform.end"
    assert event.tool == "dbt"
    assert event.status == "success"
    assert event.metrics == {"models_run": 3}
    assert event.correlation.run_id == "run-t1"
    assert event.correlation.asset_key == "gold.customers"
    assert event.correlation.job_name == "transform_job"
    assert event.correlation.trace_id == "t1"


def test_publish_emitter_merges_bound_correlation_context() -> None:
    bus = RecordingBus()
    emitter = PublishEventEmitter(
        PublishEventContext(
            asset_key="gold.orders",
            run_id="run-p1",
            partition_key="2026-03-08",
            target_system="warehouse",
        ),
        hook_bus=bus,
    )

    bind_context(trace_id="p1", span_id="ps1", job_name="publish_job")
    try:
        emitter.emit_end(status="success", metrics={"tables_published": 2})
    finally:
        clear_context()

    event = bus.events[0]
    assert event.event_type == "publish.end"
    assert event.status == "success"
    assert event.correlation.run_id == "run-p1"
    assert event.correlation.asset_key == "gold.orders"
    assert event.correlation.partition_key == "2026-03-08"
    assert event.correlation.trace_id == "p1"


def test_quality_result_emitter_merges_bound_correlation_context() -> None:
    bus = RecordingBus()
    emitter = QualityResultEventEmitter(
        QualityResultEventContext(
            asset_key="silver.orders",
            run_id="run-q1",
            partition_key="2026-03-08",
        ),
        hook_bus=bus,
    )

    bind_context(trace_id="q1", span_id="qs1", job_name="quality_job")
    try:
        emitter.emit_result(
            check_name="not_null_order_id",
            passed=True,
            severity="error",
            check_type="pandera",
        )
    finally:
        clear_context()

    event = bus.events[0]
    assert event.event_type == "quality.result"
    assert event.check_name == "not_null_order_id"
    assert event.passed is True
    assert event.severity == "error"
    assert event.correlation.run_id == "run-q1"
    assert event.correlation.asset_key == "silver.orders"
    assert event.correlation.check_name == "not_null_order_id"
    assert event.correlation.trace_id == "q1"


def test_lineage_emitter_merges_bound_correlation_context() -> None:
    bus = RecordingBus()
    emitter = LineageEventEmitter(
        LineageEventContext(),
        hook_bus=bus,
    )

    bind_context(trace_id="l1", span_id="ls1", job_name="lineage_job")
    try:
        emitter.emit_edges(
            edges=[("a", "b"), ("b", "c")],
            asset_keys=["a", "b", "c"],
            metadata={"source": "dbt"},
        )
    finally:
        clear_context()

    event = bus.events[0]
    assert event.event_type == "lineage.edges"
    assert event.edges == [("a", "b"), ("b", "c")]
    assert event.asset_keys == ["a", "b", "c"]
    assert event.metadata == {"source": "dbt"}
    assert event.correlation.trace_id == "l1"
    assert event.correlation.job_name == "lineage_job"


def test_telemetry_emitter_merges_bound_correlation_context() -> None:
    bus = RecordingBus()
    emitter = TelemetryEventEmitter(
        TelemetryEventContext(),
        hook_bus=bus,
    )

    bind_context(trace_id="tm1", span_id="tms1", job_name="telemetry_job")
    try:
        emitter.emit_metric(name="rows_processed", value=1000, unit="rows")
    finally:
        clear_context()

    event = bus.events[0]
    assert event.event_type == "telemetry.metric"
    assert event.name == "rows_processed"
    assert event.value == 1000
    assert event.unit == "rows"
    assert event.correlation.trace_id == "tm1"
    assert event.correlation.job_name == "telemetry_job"


def test_service_lifecycle_emitter_merges_bound_correlation_context() -> None:
    bus = RecordingBus()
    emitter = ServiceLifecycleEventEmitter(
        ServiceLifecycleEventContext(
            service_name="dagster-webserver",
            project_name="phlo",
            container_name="dagster-webserver-1",
        ),
        hook_bus=bus,
    )

    bind_context(trace_id="sl1", span_id="sls1", job_name="service_job")
    try:
        emitter.emit(phase="start", status="healthy", metadata={"port": 3000})
    finally:
        clear_context()

    event = bus.events[0]
    assert event.event_type == "service.start"
    assert event.service_name == "dagster-webserver"
    assert event.phase == "start"
    assert event.status == "healthy"
    assert event.metadata == {"port": 3000}
    assert event.tags["service"] == "dagster-webserver"
    assert event.correlation.trace_id == "sl1"
    assert event.correlation.job_name == "service_job"


def test_schema_migration_emitter_merges_bound_correlation_context() -> None:
    bus = RecordingBus()
    emitter = SchemaMigrationEventEmitter(
        SchemaMigrationEventContext(table_name="orders"),
        hook_bus=bus,
    )

    bind_context(trace_id="sm1", span_id="sms1", job_name="migration_job")
    try:
        emitter.emit(
            status="applied",
            classification="breaking",
            change_count=2,
            changes=[{"column": "price", "action": "drop"}],
        )
    finally:
        clear_context()

    event = bus.events[0]
    assert event.event_type == "schema_migration.applied"
    assert event.table_name == "orders"
    assert event.classification == "breaking"
    assert event.change_count == 2
    assert event.changes == [{"column": "price", "action": "drop"}]
    assert event.correlation.trace_id == "sm1"
    assert event.correlation.job_name == "migration_job"


def test_data_migration_emitter_merges_bound_correlation_context() -> None:
    bus = RecordingBus()
    emitter = DataMigrationEventEmitter(
        DataMigrationEventContext(
            migration_name="backfill_orders",
            source_type="postgres",
            destination_table="silver.orders",
            run_id="run-dm1",
        ),
        hook_bus=bus,
    )

    bind_context(trace_id="dm1", span_id="dms1", job_name="data_migration_job")
    try:
        emitter.emit(
            status="completed",
            rows_read=5000,
            rows_written=4999,
            chunk_index=3,
        )
    finally:
        clear_context()

    event = bus.events[0]
    assert event.event_type == "data_migration.completed"
    assert event.migration_name == "backfill_orders"
    assert event.source_type == "postgres"
    assert event.destination_table == "silver.orders"
    assert event.rows_read == 5000
    assert event.rows_written == 4999
    assert event.chunk_index == 3
    assert event.tags["source_type"] == "postgres"
    assert event.correlation.run_id == "run-dm1"
    assert event.correlation.trace_id == "dm1"
    assert event.correlation.job_name == "data_migration_job"
