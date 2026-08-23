"""Constants for plugin discovery registry type configuration.

Each entry maps a capability kind to its registry attribute name,
singular slug, and human-readable label. The tuple shape is positional;
consumers index into it rather than using named fields.

Internal constants module for the plugin discovery registry.
"""

from __future__ import annotations

TYPE_CONFIG = {
    "source_connectors": ("_sources", "source", "Source connector"),
    "quality_checks": ("_quality_checks", "quality", "Quality check"),
    "transformations": ("_transformations", "transformation", "Transformation"),
    "services": ("_services", "service", "Service"),
    "cli_commands": ("_cli_commands", "cli", "CLI command"),
    "hooks": ("_hooks", "hooks", "Hook"),
    "asset_providers": ("_assets", "assets", "Asset provider"),
    "resource_providers": ("_resources", "resources", "Resource provider"),
    "orchestrators": ("_orchestrators", "orchestrators", "Orchestrator"),
    "catalogs": ("_catalogs", "catalogs", "Catalog"),
}
