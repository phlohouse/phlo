"""Airbyte plugin registrations: service, assets, and ingestion provider."""

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

AirbyteServicePlugin = service_plugin_class(
    "AirbyteServicePlugin",
    name="airbyte",
    version="0.1.0",
    description="Self-managed Airbyte control plane for connector-managed ingestion",
    author="Phlo Team",
    tags=["ingestion", "airbyte", "connectors"],
)


class AirbyteAssetProvider(AssetProviderPlugin):
    """Expose registered Airbyte connection assets to the orchestrator."""

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for the Airbyte asset provider."""
        return PluginMetadata(
            name="airbyte",
            version="0.1.0",
            description="Airbyte connection assets for Phlo",
        )

    def get_assets(self) -> Iterable[AssetSpec]:
        """Return Airbyte assets registered via the decorator."""
        from phlo_airbyte.assets import get_airbyte_assets

        return get_airbyte_assets()

    def get_checks(self) -> Iterable[AssetCheckSpec]:
        """Airbyte connections do not register checks directly."""
        return []

    def clear_registries(self) -> None:
        """Reset the asset registry (tests and plugin reloads)."""
        from phlo_airbyte.assets import clear_airbyte_assets

        clear_airbyte_assets()


class AirbyteIngestionProvider(IngestionProviderPlugin):
    """Expose the Airbyte connection decorator to workflow authoring."""

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for the Airbyte ingestion provider."""
        return PluginMetadata(
            name="airbyte",
            version="0.1.0",
            description="Airbyte connector-managed ingestion provider",
        )

    def get_decorator(self) -> Callable[..., Any]:
        """Return the Airbyte connection decorator function."""
        from phlo_airbyte.assets import phlo_airbyte_connection

        return phlo_airbyte_connection

    def get_asset_retriever(self) -> Callable[[], list[Any]]:
        """Return the function retrieving registered Airbyte assets."""
        from phlo_airbyte.assets import get_airbyte_assets

        return get_airbyte_assets
