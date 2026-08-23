"""Tests for OpenMetadata hook plugin guards.

Sync must degrade gracefully: when derived client settings are missing or
raise, _get_client returns None instead of failing the hook.
"""

from __future__ import annotations

from types import SimpleNamespace

from phlo_openmetadata.hooks_plugin import OpenMetadataHookPlugin


def test_get_client_returns_none_when_configuration_is_unavailable(monkeypatch) -> None:
    """Hook plugin should skip sync when derived client settings are unavailable."""
    plugin = OpenMetadataHookPlugin()
    settings = SimpleNamespace(
        openmetadata_sync_enabled=True,
        openmetadata_username="user",
        openmetadata_password="pass",
        openmetadata_verify_ssl=True,
        openmetadata_service_name="warehouse",
        openmetadata_uri=lambda: "http://openmetadata:8585/api",
        openmetadata_database_service_type=lambda: (_ for _ in ()).throw(
            RuntimeError("missing service type")
        ),
        openmetadata_database=lambda: "warehouse",
    )

    monkeypatch.setattr(
        "phlo_openmetadata.hooks_plugin.get_openmetadata_settings", lambda: settings
    )

    assert plugin._get_client() is None
