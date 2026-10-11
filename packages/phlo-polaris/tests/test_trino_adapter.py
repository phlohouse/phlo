"""Tests for the Polaris Trino catalog adapter properties."""

from __future__ import annotations

import json

import pytest

from phlo_polaris.adapters.trino import TrinoPolarisIcebergCatalogPlugin
from phlo_polaris.settings import get_settings


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
    assert props["iceberg.rest-catalog.security"] == "OAUTH2"
    assert props["iceberg.rest-catalog.oauth2.scope"] == "PRINCIPAL_ROLE:ALL"
    assert props["iceberg.rest-catalog.oauth2.credential"] == "writer:writer-secret"
    assert props["iceberg.rest-catalog.vended-credentials-enabled"] == "true"
    assert props["s3.path-style-access"] == "true"


def test_trino_and_pyiceberg_use_saved_oauth_identity_not_principal_name(monkeypatch, tmp_path):
    from phlo_polaris.catalog_backend import _pyiceberg_catalog_config

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("POLARIS_WRITER_CLIENT_ID", "writer")
    monkeypatch.setenv("POLARIS_WRITER_CLIENT_SECRET", "unused-generated-env-secret")
    path = tmp_path / ".phlo" / "polaris-principals.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"writer": "issued-oauth-id:issued-oauth-secret"}))
    get_settings.cache_clear()
    try:
        assert (
            TrinoPolarisIcebergCatalogPlugin().get_properties()[
                "iceberg.rest-catalog.oauth2.credential"
            ]
            == "issued-oauth-id:issued-oauth-secret"
        )
        assert _pyiceberg_catalog_config()["credential"] == "issued-oauth-id:issued-oauth-secret"
        # Explicit project settings must retain their credential source after
        # the factory's project-root context has ended.
        settings = get_settings(tmp_path)
        monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path / "other-project"))
        assert settings.writer_credential() == "issued-oauth-id:issued-oauth-secret"
        monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
        path.unlink()
        monkeypatch.delenv("POLARIS_WRITER_CLIENT_SECRET")
        get_settings.cache_clear()
        with pytest.raises(ValueError, match="credentials are missing"):
            TrinoPolarisIcebergCatalogPlugin().get_properties()
    finally:
        get_settings.cache_clear()


def test_catalog_name_matches_nessie_default(monkeypatch) -> None:
    plugin = TrinoPolarisIcebergCatalogPlugin()
    assert plugin.catalog_name == "iceberg"
    assert plugin.targets == ["trino"]
