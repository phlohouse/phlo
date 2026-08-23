"""Tests that the Nessie catalog advertises its Iceberg REST compatibility
checks as capability metadata."""

from __future__ import annotations

from phlo_nessie.resource_provider import NessieResourceProvider


def test_nessie_catalog_advertises_iceberg_rest_compatibility_as_capability_metadata() -> None:
    catalog = NessieResourceProvider().get_catalogs()[0]

    compatibility = catalog.metadata["compatibility"]

    assert compatibility["target"] == "apache-iceberg-1.11"
    assert compatibility["rest_catalog"] == {"nessie_uri_suffix": "/iceberg"}
    assert compatibility["checks"] == ["nessie-iceberg-rest-uri"]
