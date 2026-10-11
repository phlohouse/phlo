"""Register run history and correlated log tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from phlo_mcp.api_client import PhloApiClient
from phlo_mcp.tracing import CanonicalTracer, trace_tool


def _register_run_tools(client: PhloApiClient, tracer: CanonicalTracer, mcp: FastMCP) -> None:
    @mcp.tool()
    @trace_tool
    def get_materialization_history(
        asset_key_path: str, limit: int = 10, cursor: str | None = None
    ) -> dict[str, Any]:
        """Get recent materializations for an asset."""
        with tracer.start_as_current_span("phlo.orchestrator.materialization_history"):
            events = client.get_materialization_history(asset_key_path, limit=limit, cursor=cursor)
            return {
                "api_base_url": client.api_base_url,
                "asset_key_path": asset_key_path,
                "events": events,
            }

    @mcp.tool()
    @trace_tool
    def get_run_logs(
        run_id: str,
        limit: int = 200,
        level: str | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Get correlated logs for a specific run or materialization."""
        with tracer.start_as_current_span("phlo.loki.run_logs"):
            payload = client.get_run_logs(run_id, limit=limit, level=level, cursor=cursor)
            return {
                "api_base_url": client.api_base_url,
                "run_id": run_id,
                "payload": payload,
            }

    @mcp.tool()
    @trace_tool
    def get_run_trace_spans(
        run_id: str, limit: int = 500, cursor: str | None = None
    ) -> dict[str, Any]:
        """Get OTEL spans correlated to a specific run id."""
        with tracer.start_as_current_span("phlo.observability.run_spans"):
            payload = client.get_run_trace_spans(run_id, limit=limit, cursor=cursor)
            return {
                "api_base_url": client.api_base_url,
                "run_id": run_id,
                "spans": payload,
            }
