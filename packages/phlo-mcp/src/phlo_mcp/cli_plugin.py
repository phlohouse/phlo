"""MCP server management CLI plugin.

Provides `phlo mcp` for serving the MCP server, inspecting its resolved
configuration (secrets redacted) and registered tools/prompts, and writing
client config snippets. Server imports are deferred to invocation so the
plugin stays importable without the MCP dependency installed.
Loaded through the phlo plugin entry-point mechanism at startup rather than imported directly.
Exposes the phlo_mcp MCP server through phlo.plugins.base CLI plugin registration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from phlo.cli.output import json_envelope
from phlo.plugins.base import cli_command_plugin_class


@click.group(name="mcp")
def mcp_group() -> None:
    """Run and inspect the Phlo MCP server."""


def _config_payload(config: Any) -> dict[str, Any]:
    return {
        "api_base_url": config.api_base_url,
        "api_token": "***" if config.api_token else None,
        "enable_write_tools": config.enable_write_tools,
        "trace_file": config.trace_file,
        "transport": config.transport,
        "host": config.host,
        "port": config.port,
        "streamable_http_path": config.streamable_http_path,
    }


def _mcp_runtime() -> tuple[Any, Any, Any]:
    try:
        from phlo_mcp.config import McpConfig, config_from_env
        from phlo_mcp.server import create_server
    except ImportError as exc:
        raise click.ClickException(
            'MCP support is not installed. Install it with: uv pip install "phlo-mcp"'
        ) from exc
    return McpConfig, config_from_env, create_server


@mcp_group.command("serve")
@click.option("--transport", type=click.Choice(["stdio", "streamable-http"]))
@click.option("--api-base-url")
@click.option("--api-token")
@click.option("--enable-write-tools", is_flag=True)
@click.option("--host")
@click.option("--port", type=int)
@click.option("--path", "streamable_http_path")
def serve_cmd(
    transport: str | None,
    api_base_url: str | None,
    api_token: str | None,
    enable_write_tools: bool,
    host: str | None,
    port: int | None,
    streamable_http_path: str | None,
) -> None:
    """Serve the Phlo MCP server."""
    McpConfig, config_from_env, create_server = _mcp_runtime()
    env = config_from_env()
    config = McpConfig(
        api_base_url=(api_base_url or env.api_base_url).rstrip("/"),
        api_token=api_token if api_token is not None else env.api_token,
        enable_write_tools=enable_write_tools or env.enable_write_tools,
        trace_file=env.trace_file,
        transport=transport or env.transport,
        host=host or env.host,
        port=port if port is not None else env.port,
        streamable_http_path=streamable_http_path or env.streamable_http_path,
    )
    create_server(config).run(transport=config.transport)


@mcp_group.command("config")
@click.option("--json", "output_json", is_flag=True, help="Emit machine-readable JSON.")
def config_cmd(output_json: bool) -> None:
    """Print resolved MCP configuration with secrets redacted."""
    _, config_from_env, _ = _mcp_runtime()
    payload = _config_payload(config_from_env())
    if output_json:
        click.echo(json_envelope(data=payload))
        return
    for key, value in payload.items():
        click.echo(f"{key}: {value}")


@mcp_group.command("tools")
@click.option("--json", "output_json", is_flag=True, help="Emit machine-readable JSON.")
def tools_cmd(output_json: bool) -> None:
    """List tools registered by the local MCP server."""
    _, config_from_env, create_server = _mcp_runtime()
    server = create_server(config_from_env())
    payload = [
        {"name": tool.name, "description": tool.description}
        for tool in server._tool_manager.list_tools()
    ]
    if output_json:
        click.echo(json_envelope(data=payload))
        return
    for tool in payload:
        click.echo(f"{tool['name']} - {tool['description'] or ''}")


@mcp_group.command("prompts")
@click.option("--json", "output_json", is_flag=True, help="Emit machine-readable JSON.")
def prompts_cmd(output_json: bool) -> None:
    """List prompts registered by the local MCP server."""
    _, config_from_env, create_server = _mcp_runtime()
    server = create_server(config_from_env())
    payload = [
        {"name": prompt.name, "description": prompt.description}
        for prompt in server._prompt_manager.list_prompts()
    ]
    if output_json:
        click.echo(json_envelope(data=payload))
        return
    for prompt in payload:
        click.echo(f"{prompt['name']} - {prompt['description'] or ''}")


@mcp_group.command("install")
@click.argument("client", type=click.Choice(["claude-code", "amp", "cursor", "vscode"]))
@click.option("--dry-run", is_flag=True, help="Print the config that would be written.")
@click.option("--json", "output_json", is_flag=True, help="Emit machine-readable JSON.")
def install_cmd(client: str, dry_run: bool, output_json: bool) -> None:
    """Print or write an MCP client configuration snippet."""
    snippet = {
        "mcpServers": {
            "phlo": {
                "command": "phlo",
                "args": ["mcp", "serve", "--transport", "stdio"],
            }
        }
    }
    target = _client_config_target(client)
    if not dry_run:
        _write_client_config(target, snippet)
    payload = {
        "client": client,
        "target": str(target),
        "dry_run": dry_run,
        "written": not dry_run,
        "config": snippet,
    }
    if output_json:
        click.echo(json_envelope(data=payload))
        return
    click.echo(json_envelope(data=payload) if dry_run else f"Wrote Phlo MCP config to {target}")


def _write_client_config(target: Path, snippet: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise click.ClickException(f"Cannot update invalid JSON config: {target}") from exc
        if isinstance(loaded, dict):
            existing = loaded
        else:
            raise click.ClickException(f"Cannot update non-object JSON config: {target}")
    servers = existing.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise click.ClickException(f"Cannot update config with non-object mcpServers: {target}")
    servers.update(snippet["mcpServers"])
    target.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _client_config_target(client: str) -> Path:
    home = Path.home()
    return {
        "claude-code": home / ".claude.json",
        "amp": home / ".config" / "amp" / "mcp.json",
        "cursor": home / ".cursor" / "mcp.json",
        "vscode": home / ".vscode" / "mcp.json",
    }[client]


McpCliPlugin = cli_command_plugin_class(
    "McpCliPlugin",
    name="mcp",
    version="0.4.0",
    description="MCP server management commands",
    commands=[mcp_group],
)

__all__ = ["McpCliPlugin"]
