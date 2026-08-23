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
    """Validate decorator retrieval."""
    provider = SlingIngestionProvider()
    decorator = provider.get_decorator()
    assert callable(decorator)


def test_sling_ingestion_provider_retriever():
    """Validate asset retriever returns callable."""
    provider = SlingIngestionProvider()
    retriever = provider.get_asset_retriever()
    assert callable(retriever)
