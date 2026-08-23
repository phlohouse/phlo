"""Tests for Traefik service plugin.

Validates service-definition defaults (name, proxy profile) and plugin
metadata tags.
"""

from phlo_traefik.plugin import TraefikServicePlugin


def test_traefik_service_definition():
    """Validate Traefik service definition defaults."""
    plugin = TraefikServicePlugin()
    defn = plugin.service_definition

    assert defn["name"] == "traefik"
    assert defn["profile"] == "proxy"


def test_traefik_plugin_metadata():
    """Validate Traefik plugin metadata."""
    plugin = TraefikServicePlugin()
    meta = plugin.metadata

    assert meta.name == "traefik"
    assert "networking" in meta.tags
