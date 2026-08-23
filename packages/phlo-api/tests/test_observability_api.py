"""Tests for observability API capability resolution.

Each test registers a mock observability backend capability and asserts
the API routes read from that capability rather than any built-in
source. The registry is cleared before and after every test, so tests
never share resolved backends.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from phlo.capabilities import (
    ObservabilityBackendSpec,
    TraceSpan,
    clear_all_capabilities,
    register_capability,
)
from phlo_api.api import observability


class _MockObservabilityBackend:
    """Mock observability backend for testing."""

    def health_summary(self):
        from phlo.capabilities.interfaces import PlatformHealthSummary

        return PlatformHealthSummary(
            overall_status="healthy",
            components={"metrics": "healthy", "maintenance": "healthy"},
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def service_status(self):
        from phlo.capabilities.interfaces import ServiceStatus

        return [
            ServiceStatus(
                name="dagster",
                status="healthy",
                last_check=datetime.now(timezone.utc).isoformat(),
            )
        ]

    def platform_metrics(self, period: str):
        from phlo.capabilities.interfaces import PlatformMetricsSummary

        return PlatformMetricsSummary(
            period=period,
            metrics={"total_operations": 10},
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def recent_alerts(self, limit: int):
        from phlo.capabilities.interfaces import AlertSummary

        return [
            AlertSummary(
                title="Test alert",
                severity="warning",
                status="firing",
                fired_at=datetime.now(timezone.utc).isoformat(),
            )
        ]

    def dashboard_links(self):
        from phlo.capabilities.interfaces import DashboardLink

        return [
            DashboardLink(
                title="Test Dashboard",
                url="http://grafana:3000/d/test",
                category="test",
            )
        ]

    def logs_query_link(self, service: str | None = None) -> str | None:
        return f"http://loki:3100/logs?service={service}" if service else "http://loki:3100/logs"

    def metrics_query_link(self, metric: str | None = None) -> str | None:
        return (
            f"http://prometheus:9090/graph?g0.expr={metric}"
            if metric
            else "http://prometheus:9090/graph"
        )


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear capability registry before and after each test."""
    clear_all_capabilities()
    yield
    clear_all_capabilities()


def test_get_health_summary_uses_capability(monkeypatch) -> None:
    """Health summary should come from the observability backend capability."""
    monkeypatch.setattr(
        observability,
        "_resolve_observability_backend",
        lambda backend=None: _MockObservabilityBackend(),
    )

    payload = observability.get_health_summary()

    assert isinstance(payload, observability.HealthSummaryResponse)
    assert payload.overall_status == "healthy"
    assert "metrics" in payload.components


def test_get_service_status_uses_capability(monkeypatch) -> None:
    """Service status should come from the observability backend capability."""
    monkeypatch.setattr(
        observability,
        "_resolve_observability_backend",
        lambda backend=None: _MockObservabilityBackend(),
    )

    payload = observability.get_service_status()

    assert isinstance(payload, list)
    assert payload[0].name == "dagster"


def test_get_platform_metrics_uses_capability(monkeypatch) -> None:
    """Platform metrics should come from the observability backend capability."""
    monkeypatch.setattr(
        observability,
        "_resolve_observability_backend",
        lambda backend=None: _MockObservabilityBackend(),
    )

    payload = observability.get_platform_metrics("24h")

    assert isinstance(payload, observability.PlatformMetricsResponse)
    assert payload.period == "24h"
    assert "total_operations" in payload.metrics


def test_get_recent_alerts_uses_capability(monkeypatch) -> None:
    """Recent alerts should come from the observability backend capability."""
    monkeypatch.setattr(
        observability,
        "_resolve_observability_backend",
        lambda backend=None: _MockObservabilityBackend(),
    )

    payload = observability.get_recent_alerts(10)

    assert isinstance(payload, list)
    assert payload[0].title == "Test alert"


def test_get_dashboard_links_uses_capability(monkeypatch) -> None:
    """Dashboard links should come from the observability backend capability."""
    monkeypatch.setattr(
        observability,
        "_resolve_observability_backend",
        lambda backend=None: _MockObservabilityBackend(),
    )

    payload = observability.get_dashboard_links()

    assert isinstance(payload, list)
    assert payload[0].title == "Test Dashboard"


def test_get_logs_query_link_uses_capability(monkeypatch) -> None:
    """Logs query link should come from the observability backend capability."""
    monkeypatch.setattr(
        observability,
        "_resolve_observability_backend",
        lambda backend=None: _MockObservabilityBackend(),
    )

    payload = observability.get_logs_query_link("test-service")

    assert "url" in payload
    assert "test-service" in payload["url"]


def test_get_metrics_query_link_uses_capability(monkeypatch) -> None:
    """Metrics query link should come from the observability backend capability."""
    monkeypatch.setattr(
        observability,
        "_resolve_observability_backend",
        lambda backend=None: _MockObservabilityBackend(),
    )

    payload = observability.get_metrics_query_link("test_metric")

    assert "url" in payload
    assert "test_metric" in payload["url"]


def test_resolve_observability_backend_uses_explicit_backend_name(monkeypatch) -> None:
    """Explicit backend parameter should resolve one provider among many."""
    backend = _MockObservabilityBackend()
    monkeypatch.setattr(observability, "discover_capabilities", lambda: None)
    register_capability(
        "observability_backend", ObservabilityBackendSpec(name="default", provider=object())
    )
    register_capability(
        "observability_backend", ObservabilityBackendSpec(name="custom", provider=backend)
    )

    resolved = observability._resolve_observability_backend("custom")

    assert resolved is backend


def test_resolve_observability_backend_uses_env_default(monkeypatch) -> None:
    """Environment variable should pick the backend when multiple are installed."""
    backend = _MockObservabilityBackend()
    monkeypatch.setattr(observability, "discover_capabilities", lambda: None)
    monkeypatch.setenv("PHLO_OBSERVABILITY_BACKEND", "custom")
    register_capability(
        "observability_backend", ObservabilityBackendSpec(name="default", provider=object())
    )
    register_capability(
        "observability_backend", ObservabilityBackendSpec(name="custom", provider=backend)
    )

    resolved = observability._resolve_observability_backend()

    assert resolved is backend


def test_resolve_observability_backend_requires_selection_when_ambiguous(monkeypatch) -> None:
    """Multiple backends without selection should return a deterministic error."""
    monkeypatch.setattr(observability, "discover_capabilities", lambda: None)
    monkeypatch.delenv("PHLO_OBSERVABILITY_BACKEND", raising=False)
    register_capability(
        "observability_backend", ObservabilityBackendSpec(name="default", provider=object())
    )
    register_capability(
        "observability_backend", ObservabilityBackendSpec(name="custom", provider=object())
    )

    with pytest.raises(RuntimeError, match="Multiple observability backends are installed"):
        observability._resolve_observability_backend()


def test_get_run_trace_spans_uses_observability_backend(monkeypatch) -> None:
    """Run trace endpoint should come from the resolved observability backend."""

    class _TraceBackend(_MockObservabilityBackend):
        def run_trace_spans(self, run_id: str, limit: int = 500):
            assert run_id == "run-123"
            assert limit == 500
            return [
                TraceSpan(
                    timestamp="2026-01-01 00:00:00",
                    trace_id="abc123",
                    span_id="span-1",
                    span_name="materialize_orders",
                    service_name="dagster",
                    span_kind="INTERNAL",
                    duration_ms=12.5,
                    status_code="STATUS_CODE_OK",
                    span_attributes={"phlo.run_id": "run-123"},
                    resource_attributes={"service.name": "dagster"},
                )
            ]

    monkeypatch.setattr(
        observability,
        "_resolve_observability_backend",
        lambda backend=None: _TraceBackend(),
    )

    payload = observability.get_run_trace_spans("run-123", limit=500)

    assert isinstance(payload, list)
    assert payload[0].trace_id == "abc123"


def test_get_trace_spans_passes_filters_to_observability_backend(monkeypatch) -> None:
    """Filtered trace endpoint should pass all supported filters to the backend."""

    class _TraceBackend(_MockObservabilityBackend):
        def trace_spans(self, filters):
            assert filters.run_id == "run-123"
            assert filters.asset_key == "silver/orders"
            assert filters.job_name == "daily_orders"
            assert filters.service_name == "dagster"
            assert filters.span_name == "materialize_orders"
            assert filters.status_code == "STATUS_CODE_ERROR"
            assert filters.start_time == "2026-04-26T00:00:00Z"
            assert filters.end_time == "2026-04-26T01:00:00Z"
            assert filters.limit == 25
            return [
                TraceSpan(
                    timestamp="2026-04-26 00:30:00",
                    trace_id="abc123",
                    span_id="span-1",
                    span_name="materialize_orders",
                    service_name="dagster",
                    status_code="STATUS_CODE_ERROR",
                    span_attributes={"phlo.asset_key": "silver/orders"},
                )
            ]

    monkeypatch.setattr(
        observability,
        "_resolve_observability_backend",
        lambda backend=None: _TraceBackend(),
    )

    payload = observability.get_trace_spans(
        run_id="run-123",
        asset_key="silver/orders",
        job_name="daily_orders",
        service_name="dagster",
        span_name="materialize_orders",
        status_code="STATUS_CODE_ERROR",
        start_time="2026-04-26T00:00:00Z",
        end_time="2026-04-26T01:00:00Z",
        limit=25,
    )

    assert isinstance(payload, list)
    assert payload[0].status_code == "STATUS_CODE_ERROR"
