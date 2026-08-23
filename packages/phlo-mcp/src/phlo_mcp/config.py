"""Configuration helpers for phlo-mcp.

Parses MCP server settings from PHLO_MCP_* environment variables into an
immutable McpConfig. Transport names are validated against a fixed set and
write tools are disabled unless explicitly enabled via env.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

_DEFAULT_API_BASE_URL = "http://127.0.0.1:4000"
Transport = Literal["stdio", "sse", "streamable-http"]
_TRANSPORTS = {"stdio", "sse", "streamable-http"}


def parse_transport(value: str) -> Transport:
    """Validate and narrow an MCP transport name."""
    if value not in _TRANSPORTS:
        raise ValueError(f"Unsupported MCP transport: {value}")
    if value == "stdio":
        return "stdio"
    if value == "sse":
        return "sse"
    return "streamable-http"


@dataclass(frozen=True, slots=True)
class McpConfig:
    """Runtime configuration for the Phlo MCP server."""

    api_base_url: str = _DEFAULT_API_BASE_URL
    api_token: str | None = None
    enable_write_tools: bool = False
    trace_file: str | None = None
    transport: Transport = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    streamable_http_path: str = "/mcp"


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def config_from_env() -> McpConfig:
    """Load MCP configuration from environment variables."""
    port = int(os.environ.get("PHLO_MCP_PORT", "8000"))
    transport = parse_transport(os.environ.get("PHLO_MCP_TRANSPORT", "stdio"))
    return McpConfig(
        api_base_url=os.environ.get("PHLO_MCP_API_BASE_URL", _DEFAULT_API_BASE_URL).rstrip("/"),
        api_token=os.environ.get("PHLO_MCP_API_TOKEN") or None,
        enable_write_tools=_truthy_env("PHLO_MCP_ENABLE_WRITE_TOOLS"),
        trace_file=os.environ.get("PHLO_MCP_TRACE_FILE") or None,
        transport=transport,
        host=os.environ.get("PHLO_MCP_HOST", "127.0.0.1"),
        port=port,
        streamable_http_path=os.environ.get("PHLO_MCP_HTTP_PATH", "/mcp"),
    )
