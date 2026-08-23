"""CLI entrypoint for phlo-mcp.

Parses transport and phlo-api connection options into McpConfig, merging CLI
flags over PHLO_MCP_* environment defaults, then builds and runs the server.
"""

from __future__ import annotations

import argparse

from phlo_mcp.config import McpConfig, config_from_env, parse_transport
from phlo_mcp.server import create_server


def build_parser() -> argparse.ArgumentParser:
    """Define the phlo-mcp server's command-line options."""
    parser = argparse.ArgumentParser(description="Run the Phlo MCP server")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        help="MCP transport to use (defaults to PHLO_MCP_TRANSPORT or stdio)",
    )
    parser.add_argument("--api-base-url", help="Base URL for the backing phlo-api instance")
    parser.add_argument("--api-token", help="Bearer token for authenticated phlo-api requests")
    parser.add_argument(
        "--enable-write-tools",
        action="store_true",
        help="Register guarded operational tools (requires authenticated phlo-api)",
    )
    parser.add_argument("--trace-file", help="Optional JSONL file to write local span events")
    parser.add_argument("--host", help="Bind host for streamable-http transport")
    parser.add_argument("--port", type=int, help="Bind port for streamable-http transport")
    parser.add_argument("--path", help="HTTP path for streamable-http transport (default: /mcp)")
    return parser


def parse_args() -> McpConfig:
    """Parse CLI arguments into McpConfig, layering flags over environment defaults."""
    parser = build_parser()
    args = parser.parse_args()
    env_config = config_from_env()
    return McpConfig(
        api_base_url=(args.api_base_url or env_config.api_base_url).rstrip("/"),
        api_token=args.api_token if args.api_token is not None else env_config.api_token,
        enable_write_tools=args.enable_write_tools or env_config.enable_write_tools,
        trace_file=args.trace_file if args.trace_file is not None else env_config.trace_file,
        transport=parse_transport(args.transport or env_config.transport),
        host=args.host or env_config.host,
        port=args.port if args.port is not None else env_config.port,
        streamable_http_path=args.path or env_config.streamable_http_path,
    )


def main() -> None:
    """Create and run the MCP server from the parsed configuration."""
    config = parse_args()
    server = create_server(config)
    server.run(transport=config.transport)


if __name__ == "__main__":
    main()
