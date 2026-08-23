"""Resource provider plugin for phlo-lineage capabilities.

This module provides the LineageResourceProvider class, which exposes phlo-lineage
capabilities through the Phlo plugin system. It enables other Phlo components to
discover and use lineage tracking functionality as a capability provider.

Capabilities Exposed:
    - LineageSinkSpec: The phlo-lineage sink for recording and querying lineage data.

Plugin Registration:
    This provider is auto-discovered via entry points. No manual registration required.

Example:
    The lineage sink is accessible through Phlo's capability system:

    >>> from phlo.capabilities import get_lineage_sink
    >>> sink = get_lineage_sink("phlo-lineage")
    >>> sink.record_asset_edges([("bronze.orders", "silver.stg_orders")])


    Lineage resource provider, loaded via the phlo.plugins.resources entry point at startup.
    Exposes the phlo_lineage lineage sink to phlo through phlo.capabilities.
"""

from __future__ import annotations

from phlo.capabilities import LineageSinkSpec
from phlo.plugins import PluginMetadata, ResourceProviderPlugin

from phlo_lineage.lineage_sink import PhloLineageSink


class LineageResourceProvider(ResourceProviderPlugin):
    """Expose phlo-lineage as a lineage sink capability."""

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for capability discovery.

        Example:
            >>> provider = LineageResourceProvider()
            >>> meta = provider.metadata
            >>> print(meta.name)
            'lineage'
            >>> print(meta.tags)
            ['lineage']
        """
        return PluginMetadata(
            name="lineage",
            version="0.1.0",
            description="Lineage sink capability provider",
            tags=["lineage"],
        )

    def get_resources(self) -> list:
        """Return no raw resources; lineage functionality is exposed only
        through the lineage_sinks capability interface."""
        return []

    def get_lineage_sinks(self) -> list[LineageSinkSpec]:
        """Expose the phlo-lineage sink as a LineageSinkSpec named
        "phlo-lineage", letting other components record and query data
        lineage through a standardized interface."""
        return [
            LineageSinkSpec(
                name="phlo-lineage",
                provider=PhloLineageSink(),
            )
        ]
