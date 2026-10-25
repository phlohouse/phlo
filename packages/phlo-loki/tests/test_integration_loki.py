"""Integration smoke tests for the phlo-loki plugin.

Marked integration: instantiate the plugin and load its service definition to
confirm both resolve without error.
"""

import pytest

pytestmark = pytest.mark.integration


def test_loki_plugin_initializes():
    """Test that Loki plugin can be instantiated."""
    from phlo_loki.plugin import LokiServicePlugin

    plugin = LokiServicePlugin()
    assert plugin is not None
    assert plugin.metadata.name == "loki"


def test_loki_service_definition():
    """Test that service definition can be loaded."""
    from phlo_loki.plugin import LokiServicePlugin

    plugin = LokiServicePlugin()
    service_def = plugin.service_definition

    assert service_def["name"] == "loki"
    assert service_def["image"].startswith("grafana/loki:3.7.4@sha256:")
    assert service_def["profile"] == "observability"
    assert service_def["compose"]["ports"] == ["${LOKI_PORT:-3100}:3100"]
