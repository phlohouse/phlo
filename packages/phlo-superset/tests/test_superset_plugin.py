"""Tests for the Superset service plugin.

Locks the pinned upstream image digest, non-root runtime user, plugin
identity, and local development settings defaults.
"""

from phlo_superset.plugin import SupersetServicePlugin
from phlo_superset.settings import SupersetSettings


def test_superset_service_definition():
    """Verify Superset plugin exposes the expected service name."""
    plugin = SupersetServicePlugin()
    defn = plugin.service_definition

    assert defn["name"] == "superset"


def test_superset_uses_pinned_upstream_image():
    definition = SupersetServicePlugin().service_definition

    assert definition["image"] == (
        "apache/superset:6.1.0@"
        "sha256:fb3464528ec7076f91195f0ff7835755aa023e281f1bb78a84782ce7a36b3705"
    )
    assert "build" not in definition
    assert definition["compose"]["user"] == "1000:1000"


def test_superset_plugin_metadata():
    """Verify Superset plugin metadata includes expected identity and tags."""
    plugin = SupersetServicePlugin()
    meta = plugin.metadata

    assert meta.name == "superset"
    assert "bi" in meta.tags


def test_superset_settings_defaults(tmp_path):
    """Verify Superset settings defaults match expected local development values."""
    settings = SupersetSettings(_project_root=tmp_path)

    assert settings.superset_port == 10007
    assert settings.superset_admin_user == "admin"
