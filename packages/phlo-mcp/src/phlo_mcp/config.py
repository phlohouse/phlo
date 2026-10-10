"""Configuration helpers for phlo-mcp.

Parses MCP server settings from PHLO_MCP_* environment variables into an
immutable McpConfig. Transport names are validated against a fixed set and
write tools are disabled unless explicitly enabled via env.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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


class McpTraceSettings(BaseSettings):
    """Read the legacy trace destination independently of server configuration."""

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore", env_file=None)

    trace_file: str | None = Field(
        None,
        validation_alias="PHLO_MCP_TRACE_FILE",
        description="Legacy JSONL debug drain destination; removed in 0.19.0. Explicit trace argument wins; blank disables.",
    )


class McpSettings(McpTraceSettings, BaseSettings):
    """Resolve fresh typed MCP settings only when server configuration is requested."""

    port: int = Field(
        8000,
        validation_alias="PHLO_MCP_PORT",
        description="MCP server port, parsed with int rather than accepting decimal strings.",
    )
    transport: Transport = Field(
        "stdio",
        validation_alias="PHLO_MCP_TRANSPORT",
        description="MCP transport: stdio, sse or streamable-http; case-sensitive.",
    )
    api_base_url: str = Field(
        _DEFAULT_API_BASE_URL,
        validation_alias="PHLO_MCP_API_BASE_URL",
        description="API base URL; trailing slashes removed.",
    )
    api_token: str | None = Field(
        None,
        validation_alias="PHLO_MCP_API_TOKEN",
        description="API bearer token; blank means absent.",
    )
    enable_write_tools: bool = Field(
        False,
        validation_alias="PHLO_MCP_ENABLE_WRITE_TOOLS",
        description="Write-tool opt-in; lowercase 1/true/yes/on enable, without trimming.",
    )
    host: str = Field(
        "127.0.0.1", validation_alias="PHLO_MCP_HOST", description="MCP server bind host."
    )
    streamable_http_path: str = Field(
        "/mcp", validation_alias="PHLO_MCP_HTTP_PATH", description="Streamable HTTP endpoint path."
    )

    @field_validator("port", mode="before")
    @classmethod
    def _port(cls, value: object) -> object:
        return int(value) if isinstance(value, str) else value

    @field_validator("transport", mode="before")
    @classmethod
    def _transport(cls, value: str) -> Transport:
        return parse_transport(value)

    @field_validator("enable_write_tools", mode="before")
    @classmethod
    def _write_tools(cls, value: object) -> bool:
        return (
            value is True or isinstance(value, str) and value.lower() in {"1", "true", "yes", "on"}
        )


def config_from_env() -> McpConfig:
    """Load MCP configuration from environment variables."""
    settings = McpSettings()
    return McpConfig(
        api_base_url=settings.api_base_url.rstrip("/"),
        api_token=settings.api_token or None,
        enable_write_tools=settings.enable_write_tools,
        trace_file=settings.trace_file or None,
        transport=settings.transport,
        host=settings.host,
        port=settings.port,
        streamable_http_path=settings.streamable_http_path,
    )
