"""Tests for the Polaris service definition and capability registration."""

from __future__ import annotations

from phlo.capabilities.interfaces import SnapshotPromotionCatalog
from phlo_polaris.plugin import PolarisServicePlugin
from phlo_polaris.promotion import PolarisSnapshotPromotionCatalog
from phlo_polaris.resource_provider import POLARIS_COMPATIBILITY_METADATA, PolarisResourceProvider


def test_service_definition_is_declared_and_not_default() -> None:
    plugin = PolarisServicePlugin()
    definition = plugin.service_definition
    assert definition["name"] == "polaris"
    assert definition["category"] == "catalog"
    # Polaris is opt-in: Nessie remains the default catalog.
    assert definition["default"] is False


def test_service_image_is_digest_pinned() -> None:
    plugin = PolarisServicePlugin()
    image = plugin.service_definition["image"]
    assert image.startswith("apache/polaris:1.7.0@sha256:")


def test_provider_registers_snapshot_promotion_catalog() -> None:
    provider = PolarisResourceProvider()
    catalogs = provider.get_catalogs()
    assert len(catalogs) == 1
    catalog = catalogs[0]
    assert catalog.name == "polaris"
    assert catalog.support.supports_refs is False
    assert catalog.support.supports_snapshots is True
    assert catalog.support.supports_promote is True
    assert isinstance(catalog.provider, PolarisSnapshotPromotionCatalog)
    assert isinstance(catalog.provider, SnapshotPromotionCatalog)


def test_provider_compatibility_metadata() -> None:
    assert POLARIS_COMPATIBILITY_METADATA["rest_catalog"]["polaris_uri_suffix"] == "/api/catalog"
    provider = PolarisResourceProvider()
    assert provider.get_catalogs()[0].metadata["compatibility"] == POLARIS_COMPATIBILITY_METADATA


def test_provider_registers_scanner_and_backup_contributor() -> None:
    provider = PolarisResourceProvider()
    scanners = provider.get_catalog_scanners()
    assert scanners[0].name == "polaris"
    contributors = provider.get_backup_contributors()
    assert contributors[0].name == "polaris"
