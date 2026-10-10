"""Register runtime and documentation resources for the MCP server."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from phlo_mcp.api_client import PhloApiClient
from phlo_mcp.models import ToolContract


def _register_introspection_resources(
    mcp: FastMCP, _required_scope_for_tool: Callable[[str], str | None]
) -> None:
    @mcp.resource("phlo://docs/mcp/tools", name="mcp_tools", mime_type="application/json")
    def mcp_tools() -> list[dict[str, Any]]:
        """List registered MCP tools for self-introspection."""
        return [
            ToolContract(
                name=tool.name,
                description=tool.description,
                input_schema=getattr(tool, "parameters", None),
                output_schema=getattr(tool, "output_schema", None),
                required_scope=_required_scope_for_tool(tool.name),
            ).model_dump(mode="json")
            for tool in mcp._tool_manager.list_tools()
        ]

    @mcp.resource("phlo://docs/mcp/prompts", name="mcp_prompts", mime_type="application/json")
    def mcp_prompts() -> list[dict[str, Any]]:
        """List registered MCP prompts for self-introspection."""
        return [
            {"name": prompt.name, "description": prompt.description}
            for prompt in mcp._prompt_manager.list_prompts()
        ]

    @mcp.resource("phlo://docs/cli", name="cli_docs", mime_type="text/markdown")
    def cli_docs() -> str:
        """Return a lightweight CLI command index."""
        from phlo.cli.commands.commands import describe_commands
        from phlo.cli.main import cli

        lines = ["# Phlo CLI", ""]
        for command in describe_commands(cli):
            if command["command"].count(" ") == 1:
                lines.append(f"- `{command['command']}` — {command['description']}")
        return "\n".join(lines)


def _register_resources(
    client: PhloApiClient,
    mcp: FastMCP,
    _required_scope_for_tool: Callable[[str], str | None],
) -> None:
    @mcp.resource(
        "phlo://runtime/config",
        name="runtime_config",
        mime_type="application/json",
    )
    def runtime_config() -> dict[str, Any] | list[dict[str, Any]]:
        """Read the current phlo project configuration."""
        return client.get_config()

    @mcp.resource(
        "phlo://runtime/services",
        name="runtime_services",
        mime_type="application/json",
    )
    def runtime_services() -> dict[str, Any] | list[dict[str, Any]]:
        """Read discovered service metadata."""
        return client.get_services()

    @mcp.resource(
        "phlo://runtime/plugins",
        name="runtime_plugins",
        mime_type="application/json",
    )
    def runtime_plugins() -> dict[str, Any] | list[dict[str, Any]]:
        """Read installed plugin metadata."""
        return client.get_plugins()

    @mcp.resource(
        "phlo://runtime/assets",
        name="runtime_assets",
        mime_type="application/json",
    )
    def runtime_assets() -> dict[str, Any] | list[dict[str, Any]]:
        """Read asset metadata."""
        return client.get_assets()

    @mcp.resource(
        "phlo://runtime/contracts",
        name="runtime_contracts",
        mime_type="application/json",
    )
    def runtime_contracts() -> dict[str, Any] | list[dict[str, Any]]:
        """Read schema contract metadata."""
        return client.get_contracts()

    @mcp.resource(
        "phlo://runtime/dashboards",
        name="runtime_dashboards",
        mime_type="application/json",
    )
    def runtime_dashboards() -> dict[str, Any] | list[dict[str, Any]]:
        """Read observability dashboard metadata."""
        return client.get_dashboard_links()

    @mcp.resource(
        "phlo://runtime/services/{service_name}",
        name="runtime_service",
        mime_type="application/json",
    )
    def runtime_service(service_name: str) -> dict[str, Any] | list[dict[str, Any]]:
        """Read metadata for one service."""
        return client.get_service_info(service_name)

    @mcp.resource(
        "phlo://runtime/assets/{asset_key_path}",
        name="runtime_asset",
        mime_type="application/json",
    )
    def runtime_asset(asset_key_path: str) -> dict[str, Any] | list[dict[str, Any]]:
        """Read metadata for one asset."""
        return client.get_asset_details(asset_key_path)

    @mcp.resource(
        "phlo://runtime/schemas/{asset_key_path}",
        name="runtime_asset_schema",
        mime_type="application/json",
    )
    def runtime_asset_schema(asset_key_path: str) -> dict[str, Any]:
        """Read schema metadata for one asset."""
        details = client.get_asset_details(asset_key_path)
        if not isinstance(details, dict):
            return {"asset_key_path": asset_key_path, "columns": []}
        return {
            "asset_key_path": asset_key_path,
            "columns": details.get("columns") or [],
            "column_lineage": details.get("column_lineage"),
            "partition_definition": details.get("partition_definition"),
        }

    @mcp.resource(
        "phlo://runtime/operations/{operation_id}",
        name="runtime_operation_context",
        mime_type="application/json",
    )
    def runtime_operation_context(operation_id: str) -> dict[str, Any] | list[dict[str, Any]]:
        """Read stable observability context for one Phlo operation."""
        return client.get_operation_context(operation_id)

    @mcp.resource(
        "phlo://runtime/contracts/{table_name}",
        name="runtime_contract",
        mime_type="application/json",
    )
    def runtime_contract(table_name: str) -> dict[str, Any] | list[dict[str, Any]]:
        """Read one schema contract."""
        return client.get_contract(table_name)

    @mcp.resource(
        "phlo://docs/packages/{package_name}",
        name="package_docs",
        mime_type="text/markdown",
    )
    def package_docs(package_name: str) -> str:
        """Read package documentation from the local package reference."""
        return _read_package_doc(package_name)

    _register_introspection_resources(mcp, _required_scope_for_tool)


def _read_package_doc(package_name: str) -> str:
    safe_name = package_name.removesuffix(".md")
    if "/" in safe_name or "\\" in safe_name or safe_name in {"", ".", ".."}:
        return f"# {package_name}\n\nPackage documentation not found.\n"
    for base in (Path.cwd(), *Path.cwd().parents):
        candidate = base / "docs" / "reference" / "packages.md"
        if candidate.is_file():
            lines = candidate.read_text(encoding="utf-8").splitlines(keepends=True)
            heading = f"## {safe_name}"
            try:
                start = next(
                    index for index, line in enumerate(lines) if line.rstrip("\r\n") == heading
                )
            except StopIteration:
                return f"# {package_name}\n\nPackage documentation not found.\n"
            end = next(
                (
                    index
                    for index, line in enumerate(lines[start + 1 :], start + 1)
                    if line.startswith("## ")
                ),
                len(lines),
            )
            section = "".join(lines[start + 1 : end]).strip()
            return f"# {safe_name}\n\n{section}\n"
    return f"# {package_name}\n\nPackage documentation not found.\n"
