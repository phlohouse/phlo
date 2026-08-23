"""Tests for OpenMetadata resource provider.

Verifies metadata-catalog capability registration, forwarding of lineage
edges and quality results to OpenMetadata, and resolution of OpenMetadata as
a metadata catalog provider through global capability discovery.
"""

from unittest.mock import Mock, patch

from phlo.capabilities import clear_all_capabilities, resolve_capability
from phlo.capabilities.discovery import discover_capabilities
from phlo.hooks import QualityResultEvent
from phlo_openmetadata.resource_provider import OpenMetadataResourceProvider


def test_resource_provider_registers_metadata_catalog_spec():
    """OpenMetadata resource provider exposes a metadata catalog capability."""
    provider = OpenMetadataResourceProvider()

    specs = provider.get_metadata_catalogs()

    assert len(specs) == 1
    assert specs[0].name == "openmetadata"
    assert specs[0].provider is not None


def test_metadata_catalog_provider_publishes_lineage_edges():
    """Metadata catalog provider forwards lineage edges to OpenMetadata."""
    provider = OpenMetadataResourceProvider().get_metadata_catalogs()[0].provider
    client = Mock()

    with patch.object(provider, "_get_client", return_value=client):
        provider.publish_lineage_edges(edges=[("raw.orders", "silver.orders")])

    client.create_lineage.assert_called_once_with("raw.orders", "silver.orders")


def test_metadata_catalog_provider_publishes_quality_result():
    """Metadata catalog provider forwards quality results to OpenMetadata."""
    provider = OpenMetadataResourceProvider().get_metadata_catalogs()[0].provider
    client = Mock()
    client.create_test_case.return_value = {"fullyQualifiedName": "raw.orders.not_null"}
    event = QualityResultEvent(
        event_type="quality.result",
        asset_key="raw.orders",
        check_name="not_null",
        check_type="NullCheck",
        passed=True,
        metadata={"metric_value": 0},
    )

    with patch.object(provider, "_get_client", return_value=client):
        provider.publish_quality_result(event=event)

    client.create_test_definition.assert_called_once()
    client.create_test_case.assert_called_once()
    client.publish_test_result.assert_called_once()


def test_openmetadata_metadata_catalog_capability_resolves():
    """Capability discovery exposes OpenMetadata as a metadata catalog provider."""
    clear_all_capabilities()
    discover_capabilities()
    resolution = resolve_capability("metadata_catalog", "openmetadata")

    assert resolution is not None
    assert resolution.name == "openmetadata"
