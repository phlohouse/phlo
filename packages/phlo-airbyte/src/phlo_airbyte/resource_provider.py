"""Capability provider exposing the Airbyte client as a runtime resource."""

from __future__ import annotations

from phlo.capabilities import ResourceSpec
from phlo.plugins.base import PluginMetadata, ResourceProviderPlugin

from phlo_airbyte.client import AirbyteClient


class AirbyteResourceProvider(ResourceProviderPlugin):
    """Expose the Airbyte Configuration API client as a capability resource."""

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for the Airbyte resource provider."""
        return PluginMetadata(
            name="airbyte",
            version="0.1.0",
            description="Airbyte API client resource",
        )

    def get_resources(self) -> list[ResourceSpec]:
        """Expose the raw Airbyte client as a runtime resource."""
        return [ResourceSpec(name="airbyte", resource=AirbyteClient())]
