"""Tests for the Sling plugin classes.

Checks that both providers register under the name "sling" and that the
ingestion provider hands out callable decorator and asset-retriever
entry points.
"""

from phlo_sling.plugin import SlingAssetProvider, SlingIngestionProvider


def test_sling_asset_provider_metadata():
    """Validate asset provider metadata."""
    provider = SlingAssetProvider()
    meta = provider.metadata

    assert meta.name == "sling"
    assert meta.version == "0.1.0"


def test_sling_ingestion_provider_metadata():
    """Validate ingestion provider metadata."""
    provider = SlingIngestionProvider()
    meta = provider.metadata

    assert meta.name == "sling"
    assert meta.version == "0.1.0"


def test_sling_ingestion_provider_decorator():
    """Validate the provider hands out the replication decorator."""
    from phlo_sling import phlo_sling_replication

    provider = SlingIngestionProvider()

    assert provider.get_decorator() is phlo_sling_replication


def test_sling_ingestion_provider_retriever():
    """Validate the provider hands out the registered-asset retriever."""
    from phlo_sling.decorator import get_sling_assets

    provider = SlingIngestionProvider()
    retriever = provider.get_asset_retriever()

    assert retriever is get_sling_assets
    assert retriever() == get_sling_assets()
