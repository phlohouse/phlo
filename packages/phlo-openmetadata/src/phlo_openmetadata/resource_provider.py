"""Resource provider plugin for OpenMetadata capabilities.

Exposes OpenMetadata as a metadata catalog capability that can be discovered
and used by the phlo capability system for publishing metadata, lineage,
and quality results.

Example:
    >>> from phlo_openmetadata.resource_provider import OpenMetadataResourceProvider
    >>> provider = OpenMetadataResourceProvider()
    >>> catalogs = provider.get_metadata_catalogs()
    >>> len(catalogs)
    1

Loaded through the phlo plugin entry-point mechanism at startup rather than
imported directly; registers its metadata catalog through phlo.capabilities.
"""

from __future__ import annotations

from phlo.capabilities import MetadataCatalogSpec
from phlo.plugins import PluginMetadata, ResourceProviderPlugin

from phlo_openmetadata.metadata_catalog import OpenMetadataCatalogProvider


class OpenMetadataResourceProvider(ResourceProviderPlugin):
    """Expose OpenMetadata as a metadata catalog capability.

    This plugin registers OpenMetadataCatalogProvider with the phlo
    capability system, allowing other components to publish metadata
    to OpenMetadata without direct coupling.

    Example:
        >>> provider = OpenMetadataResourceProvider()
        >>> provider.metadata.name
        'openmetadata'

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for resource discovery."""
        return PluginMetadata(
            name="openmetadata",
            version="0.1.0",
            description="OpenMetadata capability provider",
            tags=["catalog", "metadata"],
        )

    def get_resources(self) -> list:
        """OpenMetadata does not expose raw resources in this slice."""
        return []

    def get_metadata_catalogs(self) -> list[MetadataCatalogSpec]:
        """Expose OpenMetadata as a metadata catalog capability."""
        return [
            MetadataCatalogSpec(
                name="openmetadata",
                provider=OpenMetadataCatalogProvider(),
            )
        ]
