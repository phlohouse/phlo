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
    from phlo_airbyte.assets import phlo_airbyte_connection

    provider = AirbyteAssetProvider()
    provider.clear_registries()
    assert list(provider.get_assets()) == []

    phlo_airbyte_connection(
        connection_id="conn-1234",
        tables=["raw.posts"],
        group="ingest",
        name="posts",
    )
    try:
        assets = list(provider.get_assets())
        assert [asset.key for asset in assets] == ["ingest.posts"]
    finally:
        provider.clear_registries()


def test_resource_provider_exposes_airbyte_client() -> None:
    provider = AirbyteResourceProvider()
    resources = provider.get_resources()
    assert resources[0].name == "airbyte"


def test_ingestion_provider_wires_decorator_and_retriever() -> None:
    from phlo_airbyte.assets import phlo_airbyte_connection

    provider = AirbyteIngestionProvider()
    assert provider.get_decorator() is phlo_airbyte_connection

    phlo_airbyte_connection(
        connection_id="conn-5678", tables=["raw.events"], group="ingest", name="events"
    )
    try:
        assert [asset.key for asset in provider.get_asset_retriever()()] == ["ingest.events"]
    finally:
        AirbyteAssetProvider().clear_registries()
