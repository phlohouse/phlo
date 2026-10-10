"""MCP server exposing curated Phlo observability tools and package sections from docs/reference/packages.md."""

from __future__ import annotations

import ipaddress
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from phlo_mcp._server_prompts import _register_prompts
from phlo_mcp._server_resources import _register_resources
from phlo_mcp._tools_operations import _register_write_tools
from phlo_mcp._tools_platform import _register_platform_tools
from phlo_mcp._tools_project import _register_project_tools
from phlo_mcp._tools_runs import _register_run_tools
from phlo_mcp._tools_traces import _register_materialization_tools, _register_trace_tools
from phlo_mcp.api_client import PhloApiClient
from phlo_mcp.config import McpConfig, config_from_env
from phlo_mcp.tracing import configure_tracing, get_tracer


def create_server(config: McpConfig | None = None) -> FastMCP:
    """Create a configured FastMCP server instance."""
    resolved = config or config_from_env()
    if resolved.transport in {"sse", "streamable-http"}:
        try:
            loopback = ipaddress.ip_address(resolved.host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError(
                "MCP HTTP/SSE cannot bind to a non-loopback host without inbound authentication. "
                "Phlo MCP has no inbound authentication; PHLO_MCP_API_TOKEN only authenticates "
                "outbound API requests. Bind to 127.0.0.1 (or ::1) and use an authenticated "
                "reverse proxy for remote access."
            )
    configure_tracing(trace_file=resolved.trace_file)

    mcp = FastMCP("phlo", json_response=True)
    mcp.settings.host = resolved.host
    mcp.settings.port = resolved.port
    mcp.settings.streamable_http_path = resolved.streamable_http_path

    tracer = get_tracer()
    client = PhloApiClient(resolved)

    def _write_audit_context(
        operation: str, target: dict[str, Any], dry_run: bool
    ) -> dict[str, Any]:
        return {
            "operation": operation,
            "target": target,
            "dry_run": dry_run,
            "authenticated": bool(resolved.api_token),
            "api_base_url": client.api_base_url,
        }

    def _append_audit_record(audit_context: dict[str, Any]) -> None:
        try:
            audit_dir = Path.cwd() / ".phlo" / "audit"
            audit_dir.mkdir(parents=True, exist_ok=True)
            record = {"timestamp": datetime.now(UTC).isoformat(), **audit_context}
            with (audit_dir / "operations.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        except OSError:
            # Audit logging is best-effort: a read-only or missing workspace
            # must never fail the tool call that produced the record.
            return

    def _required_scope_for_tool(tool_name: str) -> str | None:
        if tool_name in {
            "materialize_asset",
            "retry_failed_run",
            "cancel_run",
            "backfill_asset",
        }:
            return "lakehouse:operate"
        if tool_name in {"create_workflow", "validate_workflow", "validate_schema", "lint_project"}:
            return "project:write"
        if tool_name == "install_plugin":
            return "admin"
        if (
            tool_name.startswith("get_")
            or tool_name.startswith("search_")
            or tool_name == "list_operations"
        ):
            return "lakehouse:read"
        return None

    _register_resources(client, mcp, _required_scope_for_tool)

    _register_prompts(mcp)

    _register_platform_tools(client, tracer, mcp)

    _register_run_tools(client, tracer, mcp)

    _register_trace_tools(client, tracer, mcp)

    _register_materialization_tools(client, tracer, mcp)

    _register_project_tools(client, mcp)

    # Write tools are registered only when both the flag is set and an API token
    # exists: without a token every guarded call would run unauthenticated, so the
    # tools stay unregistered rather than failing (or worse, succeeding) per call.
    _register_write_tools(resolved, _write_audit_context, _append_audit_record, client, tracer, mcp)

    return mcp
