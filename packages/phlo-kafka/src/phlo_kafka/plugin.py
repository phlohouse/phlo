"""Kafka plugin registrations: service, assets, and ingestion provider."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from phlo.capabilities import AssetCheckSpec, AssetSpec
from phlo.plugins.base import (
    AssetProviderPlugin,
    IngestionProviderPlugin,
    PluginMetadata,
    service_plugin_class,
)

KafkaServicePlugin = service_plugin_class(
    "KafkaServicePlugin",
    name="kafka",
    version="0.1.0",
    description="KRaft-mode Kafka broker for streaming ingestion into Iceberg",
    author="Phlo Team",
    tags=["ingestion", "kafka", "streaming"],
)


class KafkaAssetProvider(AssetProviderPlugin):
    """Expose registered Kafka consumer assets to the orchestrator."""

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for the Kafka asset provider."""
        return PluginMetadata(
            name="kafka",
            version="0.1.0",
            description="Kafka consumer assets for Phlo",
        )

    def get_assets(self) -> Iterable[AssetSpec]:
        """Return Kafka assets registered via the decorator."""
        from phlo_kafka.assets import get_kafka_assets

        return get_kafka_assets()

    def get_checks(self) -> Iterable[AssetCheckSpec]:
        """Kafka consumers do not register checks directly."""
        return []

    def clear_registries(self) -> None:
        """Reset the asset registry (tests and plugin reloads)."""
        from phlo_kafka.assets import clear_kafka_assets

        clear_kafka_assets()


class KafkaIngestionProvider(IngestionProviderPlugin):
    """Expose the Kafka consumer decorator to workflow authoring."""

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for the Kafka ingestion provider."""
        return PluginMetadata(
            name="kafka",
            version="0.1.0",
            description="Kafka streaming ingestion provider",
        )

    def get_decorator(self) -> Callable[..., Any]:
        """Return the Kafka consumer decorator function."""
        from phlo_kafka.assets import phlo_kafka_consumer

        return phlo_kafka_consumer

    def get_asset_retriever(self) -> Callable[[], list[Any]]:
        """Return the function retrieving registered Kafka assets."""
        from phlo_kafka.assets import get_kafka_assets

        return get_kafka_assets
