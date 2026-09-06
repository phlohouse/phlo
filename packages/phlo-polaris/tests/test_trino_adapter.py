"""Tests for the Polaris Trino catalog adapter properties."""

from __future__ import annotations

from phlo_polaris.adapters.trino import TrinoPolarisIcebergCatalogPlugin


def test_properties_configure_rest_catalog_with_oauth2(monkeypatch) -> None:
    monkeypatch.setenv("POLARIS_HOST", "polaris")
    monkeypatch.setenv("POLARIS_PORT", "10018")
    monkeypatch.setenv("POLARIS_CATALOG", "phlo")
    monkeypatch.setenv("POLARIS_WRITER_CLIENT_ID", "writer")
    monkeypatch.setenv("POLARIS_WRITER_CLIENT_SECRET", "writer-secret")

    props = TrinoPolarisIcebergCatalogPlugin().get_properties()

    assert props["connector.name"] == "iceberg"
    assert props["iceberg.catalog.type"] == "rest"
    assert props["iceberg.rest-catalog.uri"] == "http://polaris:10018/api/catalog"
    assert props["iceberg.rest-catalog.warehouse"] == "phlo"
    assert props["iceberg.rest-catalog.security.type"] == "OAUTH2"
    assert props["iceberg.rest-catalog.oauth2.credential"] == "writer:writer-secret"
    assert props["iceberg.rest-catalog.vended-credentials-enabled"] == "true"
    assert props["s3.path-style-access"] == "true"


def test_catalog_name_matches_nessie_default(monkeypatch) -> None:
    plugin = TrinoPolarisIcebergCatalogPlugin()
    assert plugin.catalog_name == "iceberg"
    assert plugin.targets == ["trino"]
