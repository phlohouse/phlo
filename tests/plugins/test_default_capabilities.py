"""Tests for the core default observability capability provider.

Verifies that capability discovery registers the default providers, that the
default observability and authorization backends expose their contracts,
and deterministic service-status ordering with links resolved from service
configuration.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from phlo.capabilities import (
    DefaultMaintenanceReadModel,
    DefaultObservabilityBackend,
    clear_all_capabilities,
    get_capability_registry,
)
from phlo.capabilities.authorization import DefaultAuthorizationPolicyBackend
from phlo.capabilities.discovery import discover_capabilities
from phlo.capabilities.interfaces import Principal, ResourceRef


class _Operation:
    def __init__(self, operation: str, status: str, completed_at: datetime, job_name: str | None):
        self.operation = operation
        self.status = status
        self.completed_at = completed_at
        self.job_name = job_name


class _Snapshot:
    def __init__(self, operations: list[_Operation]):
        self.operations = operations


def test_discover_capabilities_registers_core_default_providers() -> None:
    clear_all_capabilities()
    discover_capabilities()
    registry = get_capability_registry()
    authorization_specs = registry.list("authorization_policy_backend")
    maintenance_specs = registry.list("maintenance_read_model")
    observability_specs = registry.list("observability_backend")
    assert [spec.name for spec in authorization_specs] == ["default"]
    assert isinstance(authorization_specs[0].provider, DefaultAuthorizationPolicyBackend)
    assert [spec.name for spec in maintenance_specs] == ["default"]
    assert isinstance(maintenance_specs[0].provider, DefaultMaintenanceReadModel)
    assert [spec.name for spec in observability_specs] == ["default"]
    assert isinstance(observability_specs[0].provider, DefaultObservabilityBackend)
    assert observability_specs[0].metadata["default_stack"] == ["phlo-otel", "phlo-clickstack"]
    assert observability_specs[0].metadata["service_dependencies"] == ["clickstack"]
    assert observability_specs[0].support.supports_metrics is True
    assert observability_specs[0].support.supports_logs is True
    assert observability_specs[0].support.supports_dashboards is True
    assert observability_specs[0].support.supports_alerts is True


def test_default_observability_backend_has_required_methods() -> None:
    backend = DefaultObservabilityBackend()
    assert hasattr(backend, "health_summary")
    assert hasattr(backend, "service_status")
    assert hasattr(backend, "platform_metrics")
    assert hasattr(backend, "recent_alerts")
    assert hasattr(backend, "dashboard_links")
    assert hasattr(backend, "logs_query_link")
    assert hasattr(backend, "metrics_query_link")


def test_default_observability_backend_returns_expected_types() -> None:
    backend = DefaultObservabilityBackend()
    assert hasattr(backend.health_summary(), "overall_status")
    assert isinstance(backend.service_status(), list)
    assert hasattr(backend.platform_metrics("24h"), "period")
    assert isinstance(backend.recent_alerts(10), list)
    assert isinstance(backend.dashboard_links(), list)
    assert isinstance(backend.logs_query_link("test-service"), str) or (
        backend.logs_query_link("test-service") is None
    )
    assert isinstance(backend.metrics_query_link("test_metric"), str) or (
        backend.metrics_query_link("test_metric") is None
    )


def test_default_authorization_backend_matches_wildcard_resource_types() -> None:
    backend = DefaultAuthorizationPolicyBackend(
        policies=[
            {
                "policy_id": "table-reader",
                "effect": "allow",
                "principal": {"roles": ["analyst"]},
                "action": "dataset.read",
                "resource": {
                    "type": "table_*",
                    "id_pattern": "analytics.*",
                },
            }
        ]
    )

    allowed = backend.is_allowed(
        principal=Principal(subject="alice", principal_type="user", roles=("analyst",)),
        action="dataset.read",
        resource=ResourceRef(resource_type="table_view", resource_id="analytics.orders"),
    )

    assert allowed is True


def test_recent_alerts_limits_after_filtering_failures(monkeypatch) -> None:
    now = datetime.now(UTC)
    backend = DefaultObservabilityBackend()
    snapshot = _Snapshot(
        operations=[
            _Operation("compact", "completed", now, "dagster"),
            _Operation("expire", "completed", now - timedelta(minutes=1), "dagster"),
            _Operation("cleanup", "failed", now - timedelta(minutes=2), "dagster"),
            _Operation("vacuum", "failed", now - timedelta(minutes=3), "dagster"),
        ]
    )
    monkeypatch.setattr(backend._maintenance, "load_maintenance_status", lambda: snapshot)
    alerts = backend.recent_alerts(1)
    assert len(alerts) == 1
    assert alerts[0].title == "Maintenance operation cleanup failed"


def test_service_status_is_sorted_deterministically(monkeypatch) -> None:
    now = datetime.now(UTC)
    backend = DefaultObservabilityBackend()
    snapshot = _Snapshot(
        operations=[
            _Operation("compact", "completed", now, "zeta"),
            _Operation("expire", "completed", now - timedelta(minutes=1), "alpha"),
            _Operation("vacuum", "completed", now - timedelta(minutes=2), "alpha"),
        ]
    )
    monkeypatch.setattr(backend._maintenance, "load_maintenance_status", lambda: snapshot)
    services = backend.service_status()
    assert [service.name for service in services] == ["alpha", "zeta"]


def test_links_resolve_from_service_config(monkeypatch, tmp_path: Path) -> None:
    backend = DefaultObservabilityBackend()
    grafana_dir = tmp_path / "grafana"
    dashboards_dir = grafana_dir / "dashboards"
    dashboards_dir.mkdir(parents=True)
    (dashboards_dir / "overview.json").write_text(
        '{"uid": "phlo-overview", "title": "Phlo Lakehouse - Overview"}',
        encoding="utf-8",
    )

    services = {
        "clickstack": SimpleNamespace(
            env_vars={
                "CLICKSTACK_PORT": {"default": 18080},
                "CLICKSTACK_DASHBOARDS_PATH": {"default": "/"},
                "CLICKSTACK_LOGS_PATH": {"default": "/"},
                "CLICKSTACK_METRICS_PATH": {"default": "/"},
            },
            source_path=None,
        ),
        "grafana": SimpleNamespace(
            env_vars={
                "GRAFANA_PORT": {"default": 3003},
                "GRAFANA_DASHBOARD_PATH_TEMPLATE": {"default": "/d/{uid}"},
            },
            source_path=grafana_dir,
        ),
        "loki": SimpleNamespace(
            env_vars={
                "LOKI_PORT": {"default": 3100},
                "LOKI_LOGS_PATH": {"default": "/logs"},
            },
        ),
        "prometheus": SimpleNamespace(
            env_vars={
                "PROMETHEUS_PORT": {"default": 9090},
                "PROMETHEUS_QUERY_PATH": {"default": "/graph"},
            },
        ),
    }

    monkeypatch.setattr("phlo.capabilities.observability._discover_service", services.get)

    links = backend.dashboard_links()
    assert links[0].url == "http://localhost:18080"
    assert backend.logs_query_link("dagster") == "http://localhost:18080?service=dagster"
    assert backend.metrics_query_link("up") == "http://localhost:18080?metric=up"
