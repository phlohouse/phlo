"""Register project discovery, diagnostics and search tools."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from phlo_mcp.api_client import Environment, PhloApiClient
from phlo_mcp.tracing import trace_tool


def _register_project_tools(client: PhloApiClient, mcp: FastMCP) -> None:
    @mcp.tool()
    @trace_tool
    def list_assets(
        env: Environment,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List canonical assets in prod or staging. No free-text search is supported."""
        return {
            "api_base_url": client.api_base_url,
            "payload": client.list_assets(env, limit=limit, cursor=cursor),
        }

    @mcp.tool()
    @trace_tool
    def get_asset(asset_id: str, env: Environment) -> dict[str, Any]:
        """Read canonical asset detail in prod or staging."""
        return {"api_base_url": client.api_base_url, "payload": client.get_asset(asset_id, env)}

    @mcp.tool()
    @trace_tool
    def list_workflows(
        search: str | None = None,
        group: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List workflows discovered in the Phlo project."""
        payload = client.list_workflows(search=search, group=group, limit=limit, cursor=cursor)
        return {"api_base_url": client.api_base_url, "payload": payload}

    @mcp.tool()
    @trace_tool
    def list_templates() -> dict[str, Any]:
        """List available Phlo project templates."""
        payload = client.list_templates()
        return {"api_base_url": client.api_base_url, "payload": payload}

    @mcp.tool()
    @trace_tool
    def lint_project() -> dict[str, Any]:
        """Run lightweight Phlo project lint checks."""
        payload = client.lint_project()
        return {"api_base_url": client.api_base_url, "payload": payload}

    @mcp.tool()
    @trace_tool
    def run_doctor() -> dict[str, Any]:
        """Run Phlo doctor through phlo-api and return JSON diagnostics."""
        payload = client.run_doctor()
        return {"api_base_url": client.api_base_url, "payload": payload}

    @mcp.tool()
    @trace_tool
    def search_assets(
        query: str, env: Environment, limit: int = 20, cursor: str | None = None
    ) -> dict[str, Any]:
        """Search assets in prod or staging by ID, description, group, owner, or source."""
        payload = client.search_assets(query, env, limit=limit, cursor=cursor)
        return {"api_base_url": client.api_base_url, "query": query, "payload": payload}

    @mcp.tool()
    @trace_tool
    def search_contracts(query: str, limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        """Search Phlo contracts."""
        payload = client.search_contracts(query, limit=limit, cursor=cursor)
        return {"api_base_url": client.api_base_url, "query": query, "payload": payload}

    @mcp.tool()
    @trace_tool
    def search_runs(
        env: Environment,
        query: str | None = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Search the newest runs in prod or staging; without a query, list them."""
        payload = client.search_runs(env, query, limit=limit, cursor=cursor)
        return {"api_base_url": client.api_base_url, "query": query, "payload": payload}

    @mcp.tool()
    @trace_tool
    def search_run_logs(
        run_id: str,
        query: str,
        regex: str | None = None,
        since: str | None = None,
        until: str | None = None,
        cursor: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Search logs for one run using text and optional regex filters."""
        payload = client.search_run_logs(
            run_id,
            query=query,
            regex=regex,
            since=since,
            until=until,
            cursor=cursor,
            limit=limit,
        )
        return {"api_base_url": client.api_base_url, "run_id": run_id, "payload": payload}

    @mcp.tool()
    @trace_tool
    def follow_run_logs(run_id: str, timeout_seconds: int = 30) -> dict[str, Any]:
        """Follow run logs through a bounded phlo-api Server-Sent Event stream."""
        payload = client.follow_run_logs(
            run_id,
            timeout_seconds=timeout_seconds,
            limit=min(max(timeout_seconds * 10, 1), 200),
        )
        return {"api_base_url": client.api_base_url, "run_id": run_id, "payload": payload}

    @mcp.tool()
    @trace_tool
    def get_quality_results(
        env: Environment, asset_key: str | None = None, run_id: str | None = None
    ) -> dict[str, Any]:
        """Get quality check results in prod or staging, optionally filtered by asset or run."""
        payload = client.get_quality_results(env, asset_key=asset_key, run_id=run_id)
        return {"api_base_url": client.api_base_url, "payload": payload}

    @mcp.tool()
    @trace_tool
    def get_lineage(
        asset_key: str,
        env: Environment,
        direction: Literal["upstream", "downstream", "both"] = "both",
        depth: Annotated[int, Field(ge=1, le=10)] = 1,
    ) -> dict[str, Any]:
        """Get bounded lineage around one asset in prod or staging."""
        payload = client.get_lineage(asset_key, env, direction=direction, depth=depth)
        return {"api_base_url": client.api_base_url, "asset_key": asset_key, "payload": payload}

    @mcp.tool()
    @trace_tool
    def diff_schema(
        asset_key: str, env: Environment, from_run: str, to_run: str | None = None
    ) -> dict[str, Any]:
        """Diff the schema from_run wrote with to_run's, or with the current schema."""
        payload = client.diff_schema(asset_key, env, from_run=from_run, to_run=to_run)
        return {"api_base_url": client.api_base_url, "asset_key": asset_key, "payload": payload}
