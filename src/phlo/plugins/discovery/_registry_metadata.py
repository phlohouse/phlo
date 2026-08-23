"""Metadata serialization helpers for registry plugins.

plugin_metadata_to_dict() flattens a Plugin's PluginMetadata into the plain
dict shape served by the plugin registry and CLI listings.
Imported by the phlo.plugins.discovery registry to serialize plugin metadata for listings.
"""

from __future__ import annotations

from phlo.plugins.base import Plugin


def plugin_metadata_to_dict(plugin: Plugin) -> dict:
    """Convert plugin metadata object into API response payload."""
    metadata = plugin.metadata
    return {
        "name": metadata.name,
        "version": metadata.version,
        "description": metadata.description,
        "author": metadata.author,
        "license": metadata.license,
        "homepage": metadata.homepage,
        "tags": metadata.tags,
        "dependencies": metadata.dependencies,
        "requires_capabilities": metadata.requires_capabilities,
        "optional_capabilities": metadata.optional_capabilities,
        "support": metadata.support.to_dict(),
    }
