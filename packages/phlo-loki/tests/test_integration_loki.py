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

    assert isinstance(service_def, dict)
