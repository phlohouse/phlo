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


def test_traefik_trusted_forwarded_headers_are_configurable():
    """A TLS-terminating proxy's X-Forwarded-Proto survives only from trusted IPs."""
    defn = TraefikServicePlugin().service_definition

    assert (
        "--entrypoints.web.forwardedHeaders.trustedIPs=${TRAEFIK_TRUSTED_IPS:-}"
        in defn["compose"]["command"]
    )
    assert defn["env_vars"]["TRAEFIK_TRUSTED_IPS"]["default"] == ""
