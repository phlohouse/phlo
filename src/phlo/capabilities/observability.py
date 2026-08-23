"""Default observability capability provider owned by core.

Resolves public URLs for dashboards, logs, metrics, and query endpoints from
environment overrides with service-discovery fallbacks, and registers these
default providers when no plugin supplies its own.

Imported by the phlo.capabilities package (init and discovery); part of the core capabilities layer.
Registers default observability URL providers built on the capabilities registry and specs.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from phlo.capabilities.interfaces import (
    AlertSummary,
    DashboardLink,
    PlatformHealthSummary,
    PlatformMetricsSummary,
    ServiceStatus,
    TraceSpan,
    TraceSpanFilter,
)
from phlo.capabilities.maintenance import DefaultMaintenanceReadModel
from phlo.capabilities.registry import (
    register_capability,
)
from phlo.capabilities.specs import MaintenanceReadModelSpec, ObservabilityBackendSpec
from phlo.capabilities.support import CapabilitySupport

_PUBLIC_HOST_ENV = "PHLO_OBSERVABILITY_PUBLIC_HOST"
_PUBLIC_SCHEME_ENV = "PHLO_OBSERVABILITY_PUBLIC_SCHEME"
_CLICKSTACK_PUBLIC_URL_ENV = "CLICKSTACK_PUBLIC_URL"
_CLICKSTACK_DASHBOARDS_PATH_ENV = "CLICKSTACK_DASHBOARDS_PATH"
_CLICKSTACK_LOGS_PATH_ENV = "CLICKSTACK_LOGS_PATH"
_CLICKSTACK_METRICS_PATH_ENV = "CLICKSTACK_METRICS_PATH"
_GRAFANA_PUBLIC_URL_ENV = "GRAFANA_PUBLIC_URL"
_GRAFANA_DASHBOARD_PATH_TEMPLATE_ENV = "GRAFANA_DASHBOARD_PATH_TEMPLATE"
_PROMETHEUS_PUBLIC_URL_ENV = "PROMETHEUS_PUBLIC_URL"
_PROMETHEUS_QUERY_PATH_ENV = "PROMETHEUS_QUERY_PATH"
_LOKI_PUBLIC_URL_ENV = "LOKI_PUBLIC_URL"
_LOKI_LOGS_PATH_ENV = "LOKI_LOGS_PATH"


class DefaultObservabilityBackend:
    """Default observability backend composing metrics, logs, and dashboards."""

    def __init__(
        self,
        grafana_url: str | None = None,
        prometheus_url: str | None = None,
        loki_url: str | None = None,
    ):
        self._grafana_url = grafana_url
        self._prometheus_url = prometheus_url
        self._loki_url = loki_url
        self._maintenance = DefaultMaintenanceReadModel()

    def health_summary(self) -> PlatformHealthSummary:
        """Summarize overall and per-component health, degraded when maintenance failed."""
        try:
            maintenance = self._maintenance.load_maintenance_status()
            components = {
                "observability": "healthy",
                "maintenance": "healthy" if maintenance.operations else "no_data",
            }
            if maintenance.operations:
                failed = any(op.status == "failed" for op in maintenance.operations)
                overall = "degraded" if failed else "healthy"
            else:
                overall = "unknown"
        except Exception:
            overall = "unhealthy"
            components = {"observability": "unhealthy", "maintenance": "unhealthy"}

        return PlatformHealthSummary(
            overall_status=overall,
            components=components,
            timestamp=datetime.now(UTC).isoformat(),
        )

    def service_status(self) -> list[ServiceStatus]:
        """Report the latest status per service derived from maintenance operations."""
        try:
            maintenance = self._maintenance.load_maintenance_status()
            latest_by_service: dict[str, ServiceStatus] = {}
            for operation in maintenance.operations:
                service_name = operation.job_name or operation.operation
                if service_name in latest_by_service:
                    continue
                status = "healthy" if operation.status == "completed" else "unknown"
                latest_by_service[service_name] = ServiceStatus(
                    name=service_name,
                    status=status,
                    last_check=operation.completed_at.isoformat(),
                )
            return [latest_by_service[name] for name in sorted(latest_by_service)]
        except Exception:
            return [
                ServiceStatus(
                    name="observability",
                    status="unknown",
                    last_check=datetime.now(UTC).isoformat(),
                )
            ]

    def platform_metrics(self, period: str) -> PlatformMetricsSummary:
        """Aggregate maintenance operation counts for the requested period."""
        try:
            maintenance = self._maintenance.load_maintenance_status()
            total_ops = len(maintenance.operations)
            failed_ops = sum(1 for op in maintenance.operations if op.status == "failed")
            metrics = {
                "total_maintenance_operations": total_ops,
                "failed_operations": failed_ops,
                "successful_operations": total_ops - failed_ops,
            }
        except Exception:
            metrics = {"error": "failed_to_load_metrics"}

        return PlatformMetricsSummary(
            period=period,
            metrics=metrics,
            timestamp=datetime.now(UTC).isoformat(),
        )

    def recent_alerts(self, limit: int) -> list[AlertSummary]:
        """List recent failed maintenance operations as firing alerts, oldest trimmed to limit."""
        try:
            maintenance = self._maintenance.load_maintenance_status()
            failed_ops = [op for op in maintenance.operations if op.status == "failed"]
            return [
                AlertSummary(
                    title=f"Maintenance operation {op.operation} failed",
                    severity="error",
                    status="firing",
                    fired_at=op.completed_at.isoformat(),
                )
                for op in failed_ops[:limit]
            ]
        except Exception:
            return []

    def dashboard_links(self) -> list[DashboardLink]:
        """Return dashboard links, preferring ClickStack and falling back to Grafana."""
        clickstack_url = self._resolve_clickstack_url()
        if clickstack_url is not None:
            dashboards_path = (
                _service_env_value("clickstack", _CLICKSTACK_DASHBOARDS_PATH_ENV) or "/"
            )
            return [
                DashboardLink(
                    title="ClickStack",
                    url=_join_url(clickstack_url, dashboards_path),
                    category="overview",
                )
            ]

        grafana_url = self._grafana_url or _resolve_service_base_url(
            "grafana",
            public_url_env=_GRAFANA_PUBLIC_URL_ENV,
            port_env_key="GRAFANA_PORT",
        )
        if grafana_url is None:
            return []

        path_template = (
            _service_env_value("grafana", _GRAFANA_DASHBOARD_PATH_TEMPLATE_ENV) or "/d/{uid}"
        )
        return [
            DashboardLink(
                title=dashboard["title"],
                url=f"{grafana_url}{path_template.format(uid=dashboard['uid'])}",
                category=_dashboard_category(dashboard["title"]),
            )
            for dashboard in _discover_grafana_dashboards()
        ]

    def logs_query_link(self, service: str | None = None) -> str | None:
        """Build a logs query URL for the service via ClickStack or Loki."""
        clickstack_url = self._resolve_clickstack_url()
        if clickstack_url is not None:
            logs_path = _service_env_value("clickstack", _CLICKSTACK_LOGS_PATH_ENV) or "/"
            return _append_query_params(_join_url(clickstack_url, logs_path), service=service)

        loki_url = self._loki_url or _resolve_service_base_url(
            "loki",
            public_url_env=_LOKI_PUBLIC_URL_ENV,
            port_env_key="LOKI_PORT",
        )
        if loki_url is None:
            return None
        logs_path = _service_env_value("loki", _LOKI_LOGS_PATH_ENV) or "/logs"
        if service:
            return f"{loki_url}{logs_path}?service={service}"
        return f"{loki_url}{logs_path}"

    def metrics_query_link(self, metric: str | None = None) -> str | None:
        """Build a metrics query URL for the metric via ClickStack or Prometheus."""
        clickstack_url = self._resolve_clickstack_url()
        if clickstack_url is not None:
            metrics_path = _service_env_value("clickstack", _CLICKSTACK_METRICS_PATH_ENV) or "/"
            return _append_query_params(_join_url(clickstack_url, metrics_path), metric=metric)

        prometheus_url = self._prometheus_url or _resolve_service_base_url(
            "prometheus",
            public_url_env=_PROMETHEUS_PUBLIC_URL_ENV,
            port_env_key="PROMETHEUS_PORT",
        )
        if prometheus_url is None:
            return None
        query_path = _service_env_value("prometheus", _PROMETHEUS_QUERY_PATH_ENV) or "/graph"
        if metric:
            return f"{prometheus_url}{query_path}?g0.expr={metric}"
        return f"{prometheus_url}{query_path}"

    def run_trace_spans(self, run_id: str, limit: int = 500) -> list[TraceSpan]:
        """Return no trace spans by default.

        Backend-specific packages such as phlo-clickstack can replace the default
        observability backend with one that supports span queries.
        """
        return []

    def trace_spans(self, filters: TraceSpanFilter) -> list[TraceSpan]:
        """Return no filtered trace spans by default."""
        if filters.run_id:
            return self.run_trace_spans(filters.run_id, limit=filters.limit)
        return []

    def _resolve_clickstack_url(self) -> str | None:
        return _resolve_service_base_url(
            "clickstack",
            public_url_env=_CLICKSTACK_PUBLIC_URL_ENV,
            port_env_key="CLICKSTACK_PORT",
        )


def register_default_capability_providers() -> None:
    """Register core-owned default maintenance and observability providers."""
    register_capability(
        "maintenance_read_model",
        MaintenanceReadModelSpec(
            name="default",
            provider=DefaultMaintenanceReadModel(),
        ),
    )
    register_capability(
        "observability_backend",
        ObservabilityBackendSpec(
            name="default",
            provider=DefaultObservabilityBackend(),
            metadata={
                "default_stack": ["phlo-otel", "phlo-clickstack"],
                "service_dependencies": ["clickstack"],
            },
            support=CapabilitySupport(
                supports_metrics=True,
                supports_logs=True,
                supports_dashboards=True,
                supports_alerts=True,
            ),
        ),
    )


def _resolve_service_base_url(
    service_name: str,
    *,
    public_url_env: str,
    port_env_key: str,
) -> str | None:
    public_url = _service_env_value(service_name, public_url_env)
    if public_url:
        return public_url.rstrip("/")

    port = _service_env_value(service_name, port_env_key)
    if port is None:
        return None

    host = os.environ.get(_PUBLIC_HOST_ENV, "localhost")
    scheme = os.environ.get(_PUBLIC_SCHEME_ENV, "http")
    return f"{scheme}://{host}:{port}"


def _service_env_value(service_name: str, key: str) -> str | None:
    env_value = os.environ.get(key)
    if env_value:
        return env_value

    service = _discover_service(service_name)
    if service is None:
        return None

    payload = service.env_vars.get(key, {})
    default = payload.get("default")
    if default in (None, ""):
        return None
    return str(default)


def _discover_service(service_name: str):
    try:
        from phlo.plugins.discovery import ServiceDiscovery

        return ServiceDiscovery().get_service(service_name)
    except Exception:
        return None


def _discover_grafana_dashboards() -> list[dict[str, str]]:
    service = _discover_service("grafana")
    if service is None or service.source_path is None:
        return []

    dashboards_dir = service.source_path / "dashboards"
    if not dashboards_dir.exists():
        return []

    dashboards: list[dict[str, str]] = []
    for dashboard_path in sorted(dashboards_dir.glob("*.json")):
        payload = _load_dashboard_payload(dashboard_path)
        if payload is None:
            continue
        uid = payload.get("uid")
        title = payload.get("title")
        if isinstance(uid, str) and isinstance(title, str):
            dashboards.append({"uid": uid, "title": title})
    return dashboards


def _load_dashboard_payload(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _dashboard_category(title: str) -> str:
    lowered = title.lower()
    if "overview" in lowered:
        return "overview"
    if "infrastructure" in lowered:
        return "infrastructure"
    return "dashboard"


def _join_url(base_url: str, path: str) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    if normalized_path == "/":
        return base_url.rstrip("/")
    return f"{base_url.rstrip('/')}{normalized_path}"


def _append_query_params(url: str, **params: str | None) -> str:
    split_result = urlsplit(url)
    query_params = dict(parse_qsl(split_result.query, keep_blank_values=True))
    query_params.update({key: value for key, value in params.items() if value is not None})
    return urlunsplit(
        (
            split_result.scheme,
            split_result.netloc,
            split_result.path,
            urlencode(query_params),
            split_result.fragment,
        )
    )
