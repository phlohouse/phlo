"""Register platform health and operation-context tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from phlo_mcp.api_client import PhloApiClient
from phlo_mcp.tracing import CanonicalTracer, trace_tool


def _register_platform_tools(client: PhloApiClient, tracer: CanonicalTracer, mcp: FastMCP) -> None:
    @mcp.tool()
    @trace_tool
    def get_platform_health() -> dict[str, Any]:
        """Get Phlo platform observability health from phlo-api."""
        with tracer.start_as_current_span("phlo.observability.health"):
            payload = client.get_platform_health()
            return {"api_base_url": client.api_base_url, "payload": payload}

    @mcp.tool()
    @trace_tool
    def list_plugins() -> dict[str, Any]:
        """List installed Phlo plugins through phlo-api."""
        payload = client.get_plugins()
        return {"api_base_url": client.api_base_url, "payload": payload}

    @mcp.tool()
    @trace_tool
    def get_service_status() -> dict[str, Any]:
        """Get current service status snapshot from phlo-api."""
        with tracer.start_as_current_span("phlo.observability.services"):
            services = client.get_service_status()
            return {"api_base_url": client.api_base_url, "services": services}

    @mcp.tool()
    @trace_tool
    def get_recent_alerts(limit: int = 5, cursor: str | None = None) -> dict[str, Any]:
        """Get recent observability alerts from phlo-api."""
        with tracer.start_as_current_span("phlo.observability.alerts"):
            alerts = client.get_recent_alerts(limit, cursor=cursor)
            return {"api_base_url": client.api_base_url, "alerts": alerts}

    @mcp.tool()
    @trace_tool
    def get_dashboard_links() -> dict[str, Any]:
        """Get available observability dashboard links from phlo-api."""
        with tracer.start_as_current_span("phlo.observability.dashboards"):
            dashboards = client.get_dashboard_links()
            return {"api_base_url": client.api_base_url, "dashboards": dashboards}

    @mcp.tool()
    @trace_tool
    def list_operations(
        status: str | None = None,
        kind: str | None = None,
        query: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Find recent operations before fetching stable operation context."""
        with tracer.start_as_current_span("phlo.observability.operations"):
            payload = client.list_operations(
                status=status,
                kind=kind,
                query=query,
                limit=limit,
            )
            return {
                "api_base_url": client.api_base_url,
                "filters": {
                    "status": status,
                    "kind": kind,
                    "query": query,
                    "limit": limit,
                },
                "payload": payload,
            }

    @mcp.tool()
    @trace_tool
    def get_operation_context(operation_id: str) -> dict[str, Any]:
        """Get stable operation, trace, log, metric, and incident context."""
        with tracer.start_as_current_span("phlo.observability.operation_context"):
            payload = client.get_operation_context(operation_id)
            return {
                "api_base_url": client.api_base_url,
                "operation_id": operation_id,
                "payload": payload,
            }

    @mcp.tool()
    @trace_tool
    def get_logs_query_link(service: str | None = None) -> dict[str, Any]:
        """Get a backend-specific log query link, optionally filtered by service."""
        with tracer.start_as_current_span("phlo.observability.links.logs"):
            payload = client.get_logs_query_link(service)
            return {"api_base_url": client.api_base_url, "payload": payload}

    @mcp.tool()
    @trace_tool
    def get_metrics_query_link(metric: str | None = None) -> dict[str, Any]:
        """Get a backend-specific metrics query link, optionally filtered by metric."""
        with tracer.start_as_current_span("phlo.observability.links.metrics"):
            payload = client.get_metrics_query_link(metric)
            return {"api_base_url": client.api_base_url, "payload": payload}
