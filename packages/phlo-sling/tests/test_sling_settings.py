"""Tests for Sling settings.

Validates default values (namespace, incremental mode, auto connections)
and custom overrides.
"""

from phlo_sling.settings import SlingSettings


def test_sling_settings_defaults():
    """Validate default settings values."""
    settings = SlingSettings()

    assert settings.sling_default_namespace == "raw"
    assert settings.sling_binary_path is None
    assert settings.sling_default_mode == "incremental"
    assert settings.sling_auto_connections is True
    assert settings.sling_connections_dir is None


def test_sling_settings_custom():
    """Validate custom settings override."""
    settings = SlingSettings(
        sling_default_namespace="staging",
        sling_default_mode="full-refresh",
        sling_auto_connections=False,
    )

    assert settings.sling_default_namespace == "staging"
    assert settings.sling_default_mode == "full-refresh"
    assert settings.sling_auto_connections is False
