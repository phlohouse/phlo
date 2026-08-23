"""Integration tests for phlo-grafana.

Covers plugin initialization, service definition loading, and that the
deployed image stays pinned to an upstream Grafana release tag.
"""

import pytest

pytestmark = pytest.mark.integration


def test_grafana_plugin_initializes():
    """Test that Grafana plugin can be instantiated."""
    from phlo_grafana.plugin import GrafanaServicePlugin

    plugin = GrafanaServicePlugin()
    assert plugin is not None
    assert plugin.metadata.name == "grafana"


def test_grafana_service_definition():
    """Test that service definition can be loaded."""
    from phlo_grafana.plugin import GrafanaServicePlugin

    plugin = GrafanaServicePlugin()
    service_def = plugin.service_definition

    assert isinstance(service_def, dict)
    assert "grafana-data:/var/lib/grafana" in service_def["compose"]["volumes"]
    assert "./volumes/grafana:/var/lib/grafana" not in service_def["compose"]["volumes"]


def test_grafana_uses_pinned_upstream_image():
    from phlo_grafana.plugin import GrafanaServicePlugin

    service_def = GrafanaServicePlugin().service_definition

    assert service_def["image"] == (
        "grafana/grafana:13.1.1@"
        "sha256:7cb8c64c4d57a57e734073f3cc94620adb24a0acb929bd80ba9f14017e3a975b"
    )
    assert "build" not in service_def
    assert all(file["dest"] != "grafana/Dockerfile" for file in service_def["files"])
    assert service_def["compose"].get("user") is None
    assert service_def["compose"]["environment"]["GF_PLUGINS_PREINSTALL_DISABLED"] == "true"
