"""Core metrics runtime tests.

Covers MetricsCollector initialization and caching, default metric shapes,
asset-run queries against Postgres, table-name resolution from
materialization metadata, and Iceberg table stats handling including
malformed responses, missing dependencies, and catalog resolution.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from phlo.hooks import TelemetryEvent
from phlo.hooks.telemetry import CoreTelemetryHookProvider
from phlo.metrics import (
    AssetMetrics,
    MetricsCollector,
    MetricsDependencyError,
    MetricsMalformedResponseError,
    RunMetrics,
    SummaryMetrics,
    get_metrics_collector,
)


def test_metrics_collector_is_a_process_singleton() -> None:
    assert get_metrics_collector() is get_metrics_collector()


def test_summary_metrics_defaults() -> None:
    metrics = SummaryMetrics()
    assert metrics.total_runs_24h == 0
    assert metrics.successful_runs_24h == 0
    assert metrics.failed_runs_24h == 0
    assert metrics.assets_by_status == {"success": 0, "warning": 0, "failure": 0}


def test_asset_metrics_defaults() -> None:
    metrics = AssetMetrics(asset_name="test_asset")
    assert metrics.asset_name == "test_asset"
    assert metrics.last_run is None
    assert metrics.last_10_runs == []
    assert metrics.average_duration == 0.0
    assert metrics.failure_rate == 0.0


def test_run_metrics_creation() -> None:
    now = datetime.now(UTC)
    run = RunMetrics(
        asset_name="test_asset",
        run_id="run123",
        start_time=now,
        status="success",
        rows_processed=1000,
    )
    assert run.asset_name == "test_asset"
    assert run.run_id == "run123"
    assert run.status == "success"
    assert run.rows_processed == 1000


def test_collect_summary_computes_once_per_period_then_serves_from_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = MetricsCollector()
    prometheus_calls: list[int] = []
    postgres_calls: list[int] = []

    def _fake_prometheus(period_hours: int) -> SummaryMetrics:
        prometheus_calls.append(period_hours)
        return SummaryMetrics(total_runs_24h=7, failed_runs_24h=1)

    monkeypatch.setattr(collector, "_collect_from_prometheus", _fake_prometheus)
    monkeypatch.setattr(
        collector,
        "_collect_from_postgres",
        lambda period_hours: postgres_calls.append(period_hours) or {"rows_processed": 55},
    )

    first = collector.collect_summary(period_hours=24)
    second = collector.collect_summary(period_hours=24)

    assert prometheus_calls == [24]
    assert postgres_calls == [24]
    assert first.total_runs_24h == 7
    assert first.total_rows_processed_24h == 55
    assert second == first


def test_collect_summary_closes_postgres_connection_after_query_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed metrics query still releases its owned PostgreSQL connection."""

    class FailingConnection:
        close_calls = 0

        def cursor(self, **_kwargs):
            raise RuntimeError("query unavailable")

        def close(self) -> None:
            FailingConnection.close_calls += 1

    connection = FailingConnection()
    monkeypatch.setattr("phlo.metrics.psycopg2.connect", lambda **_kwargs: connection)
    collector = MetricsCollector()
    monkeypatch.setattr(collector, "_collect_from_prometheus", lambda _hours: SummaryMetrics())
    monkeypatch.setattr(collector, "_collect_from_iceberg", dict)

    result = collector.collect_summary(period_hours=24)

    assert result.total_rows_processed_24h == 0
    assert FailingConnection.close_calls == 1


def test_collect_summary_ignores_postgres_close_failure_after_query_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Best-effort cleanup cannot mask the existing partial-result contract."""

    class FailingConnection:
        close_calls = 0

        def cursor(self, **_kwargs):
            raise RuntimeError("query unavailable")

        def close(self) -> None:
            FailingConnection.close_calls += 1
            raise RuntimeError("close unavailable")

    connection = FailingConnection()
    monkeypatch.setattr("phlo.metrics.psycopg2.connect", lambda **_kwargs: connection)
    collector = MetricsCollector()
    monkeypatch.setattr(collector, "_collect_from_prometheus", lambda _hours: SummaryMetrics())
    monkeypatch.setattr(collector, "_collect_from_iceberg", dict)

    result = collector.collect_summary(period_hours=24)

    assert result.total_rows_processed_24h == 0
    assert FailingConnection.close_calls == 1


def test_core_telemetry_hook_provider_records_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = CoreTelemetryHookProvider()
    sink = tmp_path / "telemetry.jsonl"
    monkeypatch.setattr(provider._recorder, "path", sink)

    provider._handle_telemetry(
        TelemetryEvent(
            event_type="telemetry.metric",
            name="test.metric",
            value=1,
        )
    )

    records = [json.loads(line) for line in sink.read_text().splitlines()]
    assert [record["name"] for record in records] == ["test.metric"]


def test_core_telemetry_hook_provider_ignores_non_telemetry_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = CoreTelemetryHookProvider()
    sink = tmp_path / "telemetry.jsonl"
    monkeypatch.setattr(provider._recorder, "path", sink)

    provider._handle_telemetry({"event_type": "telemetry.log"})

    assert not sink.exists()


def test_get_asset_runs_from_postgres_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCursor:
        last_params = None

        def execute(self, _query, _params):
            FakeCursor.last_params = _params

        def fetchall(self):
            return [
                {
                    "run_id": "run-123",
                    "start_time": datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
                    "end_time": datetime(2026, 2, 1, 10, 5, tzinfo=UTC),
                    "status": "SUCCESS",
                }
            ]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConnection:
        def cursor(self, **_kwargs):
            return FakeCursor()

        def close(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("phlo.metrics.psycopg2.connect", lambda **_kwargs: FakeConnection())

    collector = MetricsCollector()
    runs = collector._get_asset_runs_from_postgres("dlt_users", limit=5)

    assert len(runs) == 1
    assert runs[0].run_id == "run-123"
    assert runs[0].status == "success"
    assert runs[0].duration_seconds == 300.0
    assert FakeCursor.last_params is not None
    assert FakeCursor.last_params[1] == r"%dlt\_users%"
    assert FakeCursor.last_params[2] == r"%dlt\_users%"


def test_resolve_asset_table_name_from_materialization_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = {
        "dagster_event": {
            "event_specific_data": {
                "materialization": {
                    "metadata_entries": [
                        {
                            "label": "table_name",
                            "entry_data": {"text": "raw.orders"},
                        }
                    ]
                }
            }
        }
    }

    class FakeCursor:
        def execute(self, _query, _params):
            return None

        def fetchall(self):
            return [{"event": json.dumps(event)}]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConnection:
        def cursor(self, **_kwargs):
            return FakeCursor()

        def close(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("phlo.metrics.psycopg2.connect", lambda **_kwargs: FakeConnection())

    collector = MetricsCollector()

    assert collector._resolve_asset_table_name_from_postgres("dlt_orders") == "raw.orders"


def test_get_iceberg_table_stats_dependency_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    collector = MetricsCollector()
    monkeypatch.setattr(
        collector,
        "_load_query_engine",
        lambda: (_ for _ in ()).throw(MetricsDependencyError("missing")),
    )
    with pytest.raises(MetricsDependencyError):
        collector._get_iceberg_table_stats("dlt_users")


def test_get_iceberg_table_stats_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeQueryEngine:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, _sql, params=None, schema=None):
            return self.rows.pop(0)

    collector = MetricsCollector()
    collector.settings.metrics_query_catalog = "iceberg"
    monkeypatch.setattr(
        collector,
        "_load_query_engine",
        lambda: FakeQueryEngine([[("bronze",)], [(1024, 42)]]),
    )
    stats = collector._get_iceberg_table_stats("dlt_users")
    assert stats == {"total_bytes": 1024, "row_count": 42}


def test_get_iceberg_table_stats_malformed_response(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeQueryEngine:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, _sql, params=None, schema=None):
            return self.rows.pop(0)

    collector = MetricsCollector()
    collector.settings.metrics_query_catalog = "iceberg"
    monkeypatch.setattr(
        collector,
        "_load_query_engine",
        lambda: FakeQueryEngine([[("bronze",)], [("bad-shape",)]]),
    )
    with pytest.raises(MetricsMalformedResponseError):
        collector._get_iceberg_table_stats("dlt_users")


def test_get_iceberg_table_stats_uses_query_engine_default_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeQueryEngine:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, _sql, params=None, schema=None):
            return self.rows.pop(0)

    collector = MetricsCollector()
    collector.settings.metrics_query_catalog = None
    monkeypatch.setattr(
        collector, "_load_query_engine", lambda: FakeQueryEngine([[("bronze",)], [(1, 2)]])
    )
    monkeypatch.setattr("phlo.metrics.discover_capabilities", lambda: None)
    monkeypatch.setattr(
        "phlo.metrics.resolve_capability",
        lambda capability, name=None: type(
            "Resolution",
            (),
            {"metadata": {"default_catalog": "lakehouse"}},
        )(),
    )

    stats = collector._get_iceberg_table_stats("dlt_users")
    assert stats == {"total_bytes": 1, "row_count": 2}


def test_get_iceberg_table_stats_requires_catalog_metadata_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = MetricsCollector()
    collector.settings.metrics_query_catalog = None
    monkeypatch.setattr("phlo.metrics.discover_capabilities", lambda: None)
    monkeypatch.setattr(
        "phlo.metrics.resolve_capability",
        lambda capability, name=None: type("Resolution", (), {"metadata": {}})(),
    )

    with pytest.raises(MetricsDependencyError, match="default catalog"):
        collector._resolve_query_engine_catalog()
