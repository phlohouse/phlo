"""Shared utilities for plugin commands.

Normalizes plugin type names between internal, CLI, and registry vocabularies,
collects installed and registry plugins, renders listing tables, and compares
versions to surface available updates.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import shutil
import subprocess
import sys
from typing import cast

from packaging.version import parse
from rich.console import Console
from rich.table import Table

from phlo.capabilities import missing_required_capabilities
from phlo.capabilities.support import coerce_capability_support
from phlo.cli.commands.plugin.scaffold import create_plugin_package  # noqa: F401
from phlo.logging import get_logger
from phlo.plugins import get_plugin_info
from phlo.plugins.base import PluginMetadata
from phlo.plugins.base.service import ServicePlugin
from phlo.plugins.discovery import get_global_registry

console = Console()
logger = get_logger(__name__)

PLUGIN_TYPE_MAP = {
    "sources": "source_connector",
    "source": "source_connector",
    "quality": "quality_check",
    "quality-providers": "quality_provider",
    "quality-provider": "quality_provider",
    "quality_provider": "quality_provider",
    "ingestion": "ingestion_provider",
    "ingestion-providers": "ingestion_provider",
    "ingestion-provider": "ingestion_provider",
    "ingestion_provider": "ingestion_provider",
    "transformation-providers": "transformation_provider",
    "transformation-provider": "transformation_provider",
    "transformation_provider": "transformation_provider",
    "transforms": "transformation",
    "transform": "transformation",
    "service": "service",
    "cli": "cli_command",
    "cli-commands": "cli_command",
    "cli-command": "cli_command",
    "cli_command": "cli_command",
    "hook": "hook",
    "assets": "asset_provider",
    "asset": "asset_provider",
    "resources": "resource_provider",
    "resource": "resource_provider",
    "orchestrator": "orchestrator",
    "catalog": "catalog",
}

PLUGIN_TYPE_CHOICES = list(PLUGIN_TYPE_MAP)

INTERNAL_TO_REGISTRY_TYPE = {
    "source_connector": "source",
    "quality_check": "quality",
    "quality_provider": "quality_provider",
    "ingestion_provider": "ingestion_provider",
    "transformation_provider": "transformation_provider",
    "transformation": "transform",
    "service": "service",
    "cli_command": "cli",
    "hook": "hook",
    "asset_provider": "assets",
    "resource_provider": "resources",
    "orchestrator": "orchestrator",
    "catalog": "catalog",
}

REGISTRY_TYPE_ALIASES = {
    "sources": "source",
    "source": "source",
    "quality": "quality",
    "quality-providers": "quality_provider",
    "quality-provider": "quality_provider",
    "quality_provider": "quality_provider",
    "ingestion": "ingestion_provider",
    "ingestion-providers": "ingestion_provider",
    "ingestion-provider": "ingestion_provider",
    "ingestion_provider": "ingestion_provider",
    "transformation-providers": "transformation_provider",
    "transformation-provider": "transformation_provider",
    "transformation_provider": "transformation_provider",
    "transforms": "transform",
    "transform": "transform",
    "service": "service",
    "cli": "cli",
    "cli-commands": "cli",
    "cli-command": "cli",
    "cli_command": "cli",
    "hook": "hook",
    "assets": "assets",
    "asset": "assets",
    "resources": "resources",
    "resource": "resources",
    "orchestrator": "orchestrator",
    "catalog": "catalog",
}


def normalize_plugin_type(plugin_type: str | None) -> str:
    """Return canonical CLI plugin type."""
    if plugin_type is None:
        return "all"
    if plugin_type == "all":
        return "all"
    internal = PLUGIN_TYPE_MAP.get(plugin_type)
    if internal is None:
        raise ValueError(f"Unknown plugin type: {plugin_type}")
    for candidate, candidate_internal in PLUGIN_TYPE_MAP.items():
        if candidate_internal == internal and candidate.endswith("s"):
            return candidate
    return plugin_type


def registry_type_for_cli(plugin_type: str | None) -> str | None:
    """Return registry-facing plugin type for a CLI type or alias."""
    if plugin_type is None:
        return None
    return REGISTRY_TYPE_ALIASES.get(plugin_type, plugin_type)


SCAFFOLD_TYPE_MAP = {
    "sources": "source",
    "source": "source",
    "quality": "quality",
    "transforms": "transform",
    "transform": "transform",
    "service": "service",
    "hook": "hook",
    "catalog": "catalog",
    "assets": "asset",
    "resources": "resource",
    "orchestrator": "orchestrator",
}


def run_pip(args: list[str], *, timeout: float = 300) -> None:
    """Install packages using uv when available, with pip fallback."""
    operation = args[0] if args else "unknown"
    if shutil.which("uv") is not None:
        command = ["uv", "pip", *args]
        installer = "uv"
    elif importlib.util.find_spec("pip") is not None:
        command = [sys.executable, "-m", "pip", *args]
        installer = "pip"
    else:
        raise RuntimeError(
            "pip module is unavailable and 'uv' is not installed; cannot install packages."
        )

    try:
        logger.info("plugin_pip_command_started", operation=operation, installer=installer)
        subprocess.run(command, check=True, timeout=timeout)
        logger.info("plugin_pip_command_succeeded", operation=operation, installer=installer)
    except subprocess.CalledProcessError as exc:
        logger.error(
            "plugin_pip_command_failed",
            operation=operation,
            installer=installer,
            return_code=exc.returncode,
        )
        raise
    except subprocess.TimeoutExpired as exc:
        message = f"Install command timed out after {timeout}s: {' '.join(command)}"
        logger.error(
            "plugin_pip_command_timed_out",
            operation=operation,
            installer=installer,
            timeout_seconds=timeout,
        )
        raise RuntimeError(message) from exc


def registry_plugin_to_dict(plugin) -> dict:
    """Convert registry plugin to dictionary."""
    return {
        "name": plugin.name,
        "type": plugin.type,
        "package": plugin.package,
        "version": plugin.version,
        "description": plugin.description,
        "author": plugin.author,
        "homepage": plugin.homepage,
        "tags": plugin.tags,
        "verified": plugin.verified,
        "core": plugin.core,
    }


def collect_installed_plugins(plugin_type: str) -> list[dict]:
    """Collect installed plugins of given type."""
    plugin_type = normalize_plugin_type(plugin_type)
    registry = get_global_registry()
    installed: list[dict] = []

    def add_plugin(plugin_key: str, name: str) -> None:
        """Append discovered plugin metadata for one registry entry."""
        info = get_plugin_info(plugin_key, name)
        if not info:
            return
        required_capabilities = info.get("requires_capabilities", [])
        optional_capabilities = info.get("optional_capabilities", [])
        support = coerce_capability_support(info.get("support"))
        missing_capabilities = missing_required_capabilities(
            PluginMetadata(
                name=info["name"],
                version=info["version"],
                requires_capabilities=list(required_capabilities),
                optional_capabilities=list(optional_capabilities),
                support=support,
            )
        )
        installed.append(
            {
                "name": info["name"],
                "type": INTERNAL_TO_REGISTRY_TYPE.get(plugin_key, plugin_key),
                "version": info["version"],
                "description": info.get("description", ""),
                "author": info.get("author", ""),
                "homepage": info.get("homepage", ""),
                "tags": info.get("tags", []),
                "installed": True,
                "required_capabilities": required_capabilities,
                "optional_capabilities": optional_capabilities,
                "support": support.to_dict(),
                "missing_capabilities": missing_capabilities,
                "ready": len(missing_capabilities) == 0,
            }
        )

    for type_key, names in registry.list_all_plugins().items():
        if plugin_type != "all" and PLUGIN_TYPE_MAP.get(plugin_type) != type_key:
            continue
        if type_key == "service":
            for name in names:
                service = cast(ServicePlugin | None, registry.get("service", name))
                if not service:
                    continue
                metadata = service.metadata
                missing_capabilities = missing_required_capabilities(metadata)
                installed.append(
                    {
                        "name": metadata.name,
                        "type": "service",
                        "version": metadata.version,
                        "description": metadata.description,
                        "author": metadata.author,
                        "homepage": metadata.homepage,
                        "tags": metadata.tags,
                        "installed": True,
                        "category": service.category,
                        "profile": service.profile,
                        "default": service.is_default,
                        "required_capabilities": metadata.requires_capabilities,
                        "optional_capabilities": metadata.optional_capabilities,
                        "support": metadata.support.to_dict(),
                        "missing_capabilities": missing_capabilities,
                        "ready": len(missing_capabilities) == 0,
                    }
                )
            continue

        for name in names:
            add_plugin(type_key, name)

    return installed


def collect_registry_plugins(plugin_type: str) -> list[dict]:
    """Collect registry plugins of given type."""
    from phlo.plugins.registry_client import list_registry_plugins

    registry_plugins = list_registry_plugins()
    plugin_type = normalize_plugin_type(plugin_type)
    if plugin_type != "all":
        # Translate CLI type to internal type first, then to registry type
        internal_type = PLUGIN_TYPE_MAP.get(plugin_type, plugin_type)
        registry_type = INTERNAL_TO_REGISTRY_TYPE.get(internal_type)
        registry_plugins = [plugin for plugin in registry_plugins if plugin.type == registry_type]
    return [registry_plugin_to_dict(plugin) for plugin in registry_plugins]


def render_plugin_table(title: str, plugins: list[dict]) -> None:
    """Render a table of plugins."""
    console.print(f"\n{title}:")
    if not plugins:
        console.print("  (none)")
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Name", style="cyan")
    table.add_column("Type", style="green")
    table.add_column("Version", style="yellow")
    table.add_column("Author", style="white")
    table.add_column("Ready", style="magenta")

    for plugin in plugins:
        ready = plugin.get("ready")
        ready_label = "yes" if ready is True else ("no" if ready is False else "n/a")
        missing = plugin.get("missing_capabilities") or []
        if ready is False and missing:
            ready_label = f"no ({', '.join(missing)})"
        table.add_row(
            plugin["name"],
            plugin["type"],
            plugin["version"],
            plugin.get("author", "unknown") or "unknown",
            ready_label,
        )

    console.print(table)


def get_installed_version(package: str) -> str | None:
    """Get installed version of a package."""
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def version_tuple(version: str) -> tuple[int, object]:
    """Convert version string to tuple for comparison."""
    try:
        return (0, parse(version))
    except Exception:
        return (0, parse("0"))


def is_version_newer(installed: str, available: str) -> bool:
    """Check if available version is newer than installed.

    When either side is not parseable as a version, any difference counts as
    an update so the user is still prompted to refresh.
    """
    try:
        return parse(available) > parse(installed)
    except Exception:
        return available != installed


def find_available_updates(registry_plugins) -> list[dict]:
    """Find available updates for installed plugins."""
    updates = []
    for plugin in registry_plugins:
        installed_version = get_installed_version(plugin.package)
        if not installed_version:
            continue
        if is_version_newer(installed_version, plugin.version):
            updates.append(
                {
                    "name": plugin.name,
                    "package": plugin.package,
                    "installed_version": installed_version,
                    "available_version": plugin.version,
                }
            )
    return updates
