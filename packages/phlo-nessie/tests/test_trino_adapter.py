"""Tests for Nessie's optional Trino catalog adapter.

Verifies the Nessie-backed catalog properties emitted for the main catalog
and that the dev catalog plugin pins the Trino prefix to the dev ref.
"""

from phlo_nessie.adapters.trino import (
    TrinoNessieIcebergCatalogPlugin,
    TrinoNessieIcebergDevCatalogPlugin,
)


def test_trino_catalog_plugin_exposes_main_catalog_properties(monkeypatch) -> None:
    """Main catalog plugin should emit Nessie-backed Trino properties."""
    monkeypatch.setenv("NESSIE_HOST", "nessie")
    monkeypatch.setenv("NESSIE_PORT", "19120")
    plugin = TrinoNessieIcebergCatalogPlugin()

    props = plugin.get_properties()

    assert plugin.catalog_name == "iceberg"
    assert props["iceberg.rest-catalog.uri"] == "http://nessie:19120/iceberg"
    assert "iceberg.rest-catalog.prefix" not in props


def test_trino_catalog_plugin_exposes_dev_catalog_prefix(monkeypatch) -> None:
    """Dev catalog plugin should pin the Trino prefix to the dev ref."""
    monkeypatch.setenv("NESSIE_HOST", "nessie")
    monkeypatch.setenv("NESSIE_PORT", "19120")
    plugin = TrinoNessieIcebergDevCatalogPlugin()

    props = plugin.get_properties()

    assert plugin.catalog_name == "iceberg_dev"
    assert props["iceberg.rest-catalog.prefix"] == "dev"
