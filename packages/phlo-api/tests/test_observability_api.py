"""Tests for observability API capability resolution.

Endpoint tests use a fake backend with deliberately distinctive payloads and
assert the handlers map the backend data faithfully (field-by-field, order
preserved). Resolution precedence (explicit name, env default, ambiguity
failure) is exercised against a registry of multiple backends.
"""

from __future__ import annotations

import pytest

from phlo.capabilities import (
    ObservabilityBackendSpec,
    TraceSpan,
    clear_all_capabilities,
    register_capability,
)
from phlo_api.api import observability


class _FakeObservabilityBackend:
    """Deterministic backend with distinctive payloads for faithful-mapping asserts."""

    def __init__(self) -> None:
        self.received_run_trace: tuple[str, int] | None = None

    def health_summary(self):
        from phlo.capabilities.interfaces import PlatformHealthSummary

        return PlatformHealthSummary(
            overall_status="degraded",
            components={"metrics": "healthy", "maintenance": "unhealthy", "lineage": "healthy"},
            timestamp="2026-04-26T00:00:00+00:00",
        )

    def service_status(self):
        from phlo.capabilities.interfaces import ServiceStatus

        return [
            ServiceStatus(name="dagster", status="healthy", last_check="2026-04-26T00:01:00+00:00"),
            ServiceStatus(name="trino", status="down", last_check="2026-04-26T00:02:00+00:00"),
        ]

    def platform_metrics(self, period: str):
        from phlo.capabilities.interfaces import PlatformMetricsSummary

        assert period == "7d", "handler must forward the requested period"
        return PlatformMetricsSummary(
            period=period,
            metrics={"total_operations": 10, "failed_runs": 2, "success_rate": 0.8},
            timestamp="2026-04-26T00:03:00+00:00",
        )

    def recent_alerts(self, limit: int):
        from phlo.capabilities.interfaces import AlertSummary

        assert limit > 0, "handler must request a bounded alert page"
        return [
            AlertSummary(
                title="Disk pressure on warehouse",
                severity="critical",
                status="firing",
                fired_at="2026-04-26T00:04:00+00:00",
            ),
            AlertSummary(
                title="Stale freshness on raw.orders",
                severity="warning",
                status="resolved",
                fired_at="2026-04-25T23:00:00+00:00",
            ),
        ]

    def dashboard_links(self):
        from phlo.capabilities.interfaces import DashboardLink

        return [
            DashboardLink(
                title="Dagster Overview", url="http://grafana:3000/d/dag", category="orchestration"
            ),
            DashboardLink(title="Loki Logs", url="http://grafana:3000/d/logs", category="logs"),
        ]

    def logs_query_link(self, service: str | None = None) -> str | None:
        if service:
            return f"http://loki:3100/logs?service={service}"
        return "http://loki:3100/logs"

    def metrics_query_link(self, metric: str | None = None) -> str | None:
        if metric:
            return f"http://prometheus:9090/graph?g0.expr={metric}"
        return "http://prometheus:9090/graph"

    def run_trace_spans(self, run_id: str, limit: int = 500):
        self.received_run_trace = (run_id, limit)
        return [
            TraceSpan(
                timestamp="2026-04-26 00:30:00",
                trace_id=f"trace-for-{run_id}",
                span_id=f"span-{run_id}",
                span_name=f"materialize_{run_id}",
                service_name="dagster",
                span_kind="INTERNAL",
                duration_ms=12.5,
                status_code="STATUS_CODE_OK",
                span_attributes={"phlo.run_id": run_id},
                resource_attributes={"service.name": "dagster"},
            )
        ]


class _FilterableObservabilityBackend(_FakeObservabilityBackend):
    """Backend that also supports the richer filtered span query."""

    def __init__(self) -> None:
        super().__init__()
        self.received_trace_filters = None

    def trace_spans(self, filters):
        self.received_trace_filters = filters
        return [
            TraceSpan(
                timestamp="2026-04-26 00:30:00",
                trace_id=f"trace-{filters.run_id or 'nofilter'}",
                span_id="span-1",
                span_name=filters.span_name or "any",
                service_name=filters.service_name or "any",
                status_code=filters.status_code or "STATUS_CODE_UNSET",
                span_attributes={"phlo.asset_key": filters.asset_key or ""},
            )
        ]


class _ExplodingObservabilityBackend:
    """Backend whose every callable raises, to prove handlers fail soft."""

    def health_summary(self):
        raise RuntimeError("backend unavailable")

    def service_status(self):
        raise RuntimeError("backend unavailable")

    def platform_metrics(self, period: str):
        raise RuntimeError("backend unavailable")

    def recent_alerts(self, limit: int):
        raise RuntimeError("backend unavailable")

    def dashboard_links(self):
        raise RuntimeError("backend unavailable")

    def logs_query_link(self, service: str | None = None) -> str | None:
        raise RuntimeError("backend unavailable")

    def metrics_query_link(self, metric: str | None = None) -> str | None:
        raise RuntimeError("backend unavailable")


_ENDPOINT_CALLS = [
    lambda backend: observability.get_health_summary(backend=backend),
    lambda backend: observability.get_service_status(backend=backend),
    lambda backend: observability.get_platform_metrics(backend=backend),
    lambda backend: observability.get_recent_alerts(backend=backend),
    lambda backend: observability.get_dashboard_links(backend=backend),
    lambda backend: observability.get_logs_query_link(backend=backend),
    lambda backend: observability.get_metrics_query_link(backend=backend),
]


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear capability registry before and after each test."""
    clear_all_capabilities()
    yield
    clear_all_capabilities()


def test_get_health_summary_maps_the_backend_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        observability,
        "_resolve_observability_backend",
        lambda backend=None: _FakeObservabilityBackend(),
    )

    payload = observability.get_health_summary()

    assert isinstance(payload, observability.HealthSummaryResponse)
    assert payload.overall_status == "degraded"
    assert payload.components == {
        "metrics": "healthy",
        "maintenance": "unhealthy",
        "lineage": "healthy",
    }
    assert payload.timestamp == "2026-04-26T00:00:00+00:00"


def test_get_service_status_preserves_order_and_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        observability,
        "_resolve_observability_backend",
        lambda backend=None: _FakeObservabilityBackend(),
    )

    payload = observability.get_service_status()

    assert [(svc.name, svc.status, svc.last_check) for svc in payload] == [
        ("dagster", "healthy", "2026-04-26T00:01:00+00:00"),
        ("trino", "down", "2026-04-26T00:02:00+00:00"),
    ]


def test_get_platform_metrics_forwards_period_and_maps_metrics(monkeypatch) -> None:
    monkeypatch.setattr(
        observability,
        "_resolve_observability_backend",
        lambda backend=None: _FakeObservabilityBackend(),
    )

    payload = observability.get_platform_metrics(period="7d")

    assert isinstance(payload, observability.PlatformMetricsResponse)
    assert payload.period == "7d"
    assert payload.metrics == {"total_operations": 10, "failed_runs": 2, "success_rate": 0.8}
    assert payload.timestamp == "2026-04-26T00:03:00+00:00"


def test_get_recent_alerts_preserves_order_and_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        observability,
        "_resolve_observability_backend",
        lambda backend=None: _FakeObservabilityBackend(),
    )

    payload = observability.get_recent_alerts(limit=10)

    assert [(alert.title, alert.severity, alert.status) for alert in payload] == [
        ("Disk pressure on warehouse", "critical", "firing"),
        ("Stale freshness on raw.orders", "warning", "resolved"),
    ]
    assert payload[1].fired_at == "2026-04-25T23:00:00+00:00"


def test_get_dashboard_links_maps_every_field(monkeypatch) -> None:
    monkeypatch.setattr(
        observability,
        "_resolve_observability_backend",
        lambda backend=None: _FakeObservabilityBackend(),
    )

    payload = observability.get_dashboard_links()

    assert [(link.title, link.url, link.category) for link in payload] == [
        ("Dagster Overview", "http://grafana:3000/d/dag", "orchestration"),
        ("Loki Logs", "http://grafana:3000/d/logs", "logs"),
    ]


def test_get_logs_query_link_returns_backend_url_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(
        observability,
        "_resolve_observability_backend",
        lambda backend=None: _FakeObservabilityBackend(),
    )

    with_service = observability.get_logs_query_link(service="dagster")
    without_service = observability.get_logs_query_link()

    assert with_service == {"url": "http://loki:3100/logs?service=dagster"}
    assert without_service == {"url": "http://loki:3100/logs"}


def test_get_metrics_query_link_returns_backend_url_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(
        observability,
        "_resolve_observability_backend",
        lambda backend=None: _FakeObservabilityBackend(),
    )

    with_metric = observability.get_metrics_query_link(metric="phlo_runs_failed_total")
    without_metric = observability.get_metrics_query_link()

    assert with_metric == {"url": "http://prometheus:9090/graph?g0.expr=phlo_runs_failed_total"}
    assert without_metric == {"url": "http://prometheus:9090/graph"}


@pytest.mark.parametrize(
    "call",
    _ENDPOINT_CALLS,
    ids=["health", "services", "metrics", "alerts", "dashboards", "log-link", "metric-link"],
)
def test_endpoints_fail_soft_when_the_backend_raises(call, monkeypatch) -> None:
    """A broken backend degrades to an error payload instead of a 500."""
    monkeypatch.setattr(
        observability,
        "_resolve_observability_backend",
        lambda backend=None: _ExplodingObservabilityBackend(),
    )

    payload = call(None)

    assert set(payload.keys()) == {"error"}
    assert "backend unavailable" in payload["error"]


def test_resolve_observability_backend_uses_explicit_backend_name(monkeypatch) -> None:
    """Explicit backend parameter should resolve one provider among many."""
    preferred = _FakeObservabilityBackend()
    monkeypatch.setattr(observability, "discover_capabilities", lambda: None)
    register_capability(
        "observability_backend",
        ObservabilityBackendSpec(name="preferred", provider=preferred),
    )
    register_capability(
        "observability_backend",
        ObservabilityBackendSpec(name="other", provider=object()),
    )
    monkeypatch.setenv("PHLO_OBSERVABILITY_BACKEND", "other")

    resolved = observability._resolve_observability_backend("preferred")

    assert resolved is preferred


def test_resolve_observability_backend_uses_env_default(monkeypatch) -> None:
    """Environment variable should pick the backend when multiple are installed."""
    selected = _FakeObservabilityBackend()
    monkeypatch.setattr(observability, "discover_capabilities", lambda: None)
    register_capability(
        "observability_backend",
        ObservabilityBackendSpec(name="fallback", provider=object()),
    )
    register_capability(
        "observability_backend",
        ObservabilityBackendSpec(name="selected", provider=selected),
    )
    monkeypatch.setenv("PHLO_OBSERVABILITY_BACKEND", "selected")

    resolved = observability._resolve_observability_backend()

    assert resolved is selected


def test_resolve_observability_backend_requires_selection_when_ambiguous(monkeypatch) -> None:
    """Multiple backends without selection should return a deterministic error."""
    monkeypatch.setattr(observability, "discover_capabilities", lambda: None)
    register_capability(
        "observability_backend",
        ObservabilityBackendSpec(name="first", provider=object()),
    )
    register_capability(
        "observability_backend",
        ObservabilityBackendSpec(name="second", provider=object()),
    )
    monkeypatch.delenv("PHLO_OBSERVABILITY_BACKEND", raising=False)

    with pytest.raises(RuntimeError, match="Multiple observability backends are installed"):
        observability._resolve_observability_backend()


def test_get_run_trace_spans_forwards_run_id_and_limit(monkeypatch) -> None:
    backend = _FakeObservabilityBackend()
    monkeypatch.setattr(
        observability,
        "_resolve_observability_backend",
        lambda _backend=None, b=backend: b,
    )

    payload = observability.get_run_trace_spans("run-abc", limit=42)

    assert backend.received_run_trace == ("run-abc", 42)
    assert payload[0].trace_id == "trace-for-run-abc"
    assert payload[0].span_attributes == {"phlo.run_id": "run-abc"}


def test_filterable_backend_is_preferred_for_run_traces(monkeypatch) -> None:
    """When the backend supports filtered spans, run traces route through them."""
    backend = _FilterableObservabilityBackend()
    monkeypatch.setattr(
        observability,
        "_resolve_observability_backend",
        lambda _backend=None, b=backend: b,
    )

    payload = observability.get_run_trace_spans("run-abc", limit=42)

    assert backend.received_run_trace is None, "legacy path must not be used"
    assert backend.received_trace_filters is not None
    assert backend.received_trace_filters.run_id == "run-abc"
    assert payload[0].trace_id == "trace-run-abc"


def test_get_trace_spans_passes_all_filters_to_the_backend(monkeypatch) -> None:
    filters_sent = {
        "run_id": "run-123",
        "asset_key": "silver/orders",
        "job_name": "daily_orders",
        "service_name": "dagster",
        "span_name": "materialize_orders",
        "status_code": "STATUS_CODE_ERROR",
        "start_time": "2026-04-26T00:00:00Z",
        "end_time": "2026-04-26T01:00:00Z",
        "limit": 25,
    }
    backend = _FilterableObservabilityBackend()
    monkeypatch.setattr(
        observability,
        "_resolve_observability_backend",
        lambda _backend=None, b=backend: b,
    )

    payload = observability.get_trace_spans(**filters_sent)

    received = backend.received_trace_filters
    for name, value in filters_sent.items():
        assert getattr(received, name) == value, f"filter {name} was not forwarded"
    assert payload[0].status_code == "STATUS_CODE_ERROR"
