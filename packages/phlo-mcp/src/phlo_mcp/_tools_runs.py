"""Register run history and correlated log tools."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from phlo_mcp.api_client import Environment, PhloApiClient
from phlo_mcp.tracing import CanonicalTracer, trace_tool


def _register_run_tools(client: PhloApiClient, tracer: CanonicalTracer, mcp: FastMCP) -> None:
    @mcp.tool()
    @trace_tool
    def list_runs(
        env: Environment,
        limit: Annotated[int, Field(ge=1, le=100)] = 100,
        job_id: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List canonical runs in prod or staging, optionally filtered by job ID or status."""
        payload = client.list_runs(env, limit=limit, job_id=job_id, status=status, cursor=cursor)
        return {"api_base_url": client.api_base_url, "payload": payload}

    @mcp.tool()
    @trace_tool
    def get_run(run_id: str, env: Environment) -> dict[str, Any]:
        """Read canonical run detail in prod or staging."""
        return {"api_base_url": client.api_base_url, "payload": client.get_run(run_id, env)}

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
