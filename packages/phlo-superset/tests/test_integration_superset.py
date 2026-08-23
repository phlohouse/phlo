"""Integration tests for phlo-superset.

Covers plugin initialization and service definition loading.
"""

import pytest

pytestmark = pytest.mark.integration


def test_superset_plugin_initializes():
    """Test that Superset plugin can be instantiated."""
    from phlo_superset.plugin import SupersetServicePlugin

    plugin = SupersetServicePlugin()
    assert plugin is not None
    assert plugin.metadata.name == "superset"


def test_superset_service_definition():
    """Test that service definition can be loaded."""
    from phlo_superset.plugin import SupersetServicePlugin

    plugin = SupersetServicePlugin()
    service_def = plugin.service_definition

    assert isinstance(service_def, dict)
