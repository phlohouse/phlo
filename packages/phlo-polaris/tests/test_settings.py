"""Tests for Polaris settings URI builders and host resolution."""

from __future__ import annotations

from phlo_polaris.settings import PolarisSettings


def test_uri_builders_resolve_host_outside_compose(monkeypatch) -> None:
    # Outside the compose network the service hostname resolves to localhost.
    monkeypatch.setenv("POLARIS_HOST", "polaris")
    monkeypatch.setenv("POLARIS_PORT", "10018")
    settings = PolarisSettings()
    assert settings.polaris_api_uri() == "http://localhost:10018"
    assert settings.polaris_rest_catalog_uri() == "http://localhost:10018/api/catalog"
    assert settings.oauth_token_uri() == "http://localhost:10018/api/catalog/v1/oauth/tokens"


def test_credentials_delegate_to_principals(monkeypatch) -> None:
    monkeypatch.setenv("POLARIS_WRITER_CLIENT_ID", "w")
    monkeypatch.setenv("POLARIS_WRITER_CLIENT_SECRET", "ws")
    monkeypatch.setenv("POLARIS_READER_CLIENT_ID", "r")
    monkeypatch.setenv("POLARIS_READER_CLIENT_SECRET", "rs")
    settings = PolarisSettings()
    assert settings.writer_credential() == "w:ws"
    assert settings.reader_credential() == "r:rs"
