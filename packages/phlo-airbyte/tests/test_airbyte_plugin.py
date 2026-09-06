"""Tests for the Airbyte service definition and plugin registrations."""

from __future__ import annotations

from phlo_airbyte.plugin import (
    AirbyteAssetProvider,
    AirbyteServicePlugin,
    AirbyteIngestionProvider,
)
from phlo_airbyte.resource_provider import AirbyteResourceProvider


def test_service_definition_is_digest_pinned_and_opt_in() -> None:
    plugin = AirbyteServicePlugin()
    definition = plugin.service_definition
    assert definition["name"] == "airbyte"
    assert definition["default"] is False
    assert definition["image"].startswith("airbyte/server:2.2.0@sha256:")


def test_asset_provider_exposes_registered_assets() -> None:
    provider = AirbyteAssetProvider()
    provider.clear_registries()
    assert list(provider.get_assets()) == []
    provider.clear_registries()


def test_resource_provider_exposes_airbyte_client() -> None:
    provider = AirbyteResourceProvider()
    resources = provider.get_resources()
    assert resources[0].name == "airbyte"


def test_ingestion_provider_wires_decorator_and_retriever() -> None:
    provider = AirbyteIngestionProvider()
    assert callable(provider.get_decorator())
    assert callable(provider.get_asset_retriever())
