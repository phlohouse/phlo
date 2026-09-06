"""Capability provider exposing the Kafka client as a runtime resource."""

from __future__ import annotations

from phlo.capabilities import ResourceSpec
from phlo.plugins.base import PluginMetadata, ResourceProviderPlugin

from phlo_kafka.resource import KafkaResource


class KafkaResourceProvider(ResourceProviderPlugin):
    """Expose the Kafka client facade as a capability resource."""

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for the Kafka resource provider."""
        return PluginMetadata(
            name="kafka",
            version="0.1.0",
            description="Kafka client resource",
        )

    def get_resources(self) -> list[ResourceSpec]:
        """Expose the Kafka client as a runtime resource."""
        return [ResourceSpec(name="kafka", resource=KafkaResource())]
