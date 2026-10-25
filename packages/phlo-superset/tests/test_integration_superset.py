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
    """Test that the service definition pins the upstream image."""
    from phlo_superset.plugin import SupersetServicePlugin

    plugin = SupersetServicePlugin()
    service_def = plugin.service_definition

    assert service_def["name"] == "superset"
    assert service_def["image"] == (
        "apache/superset:6.1.0@"
        "sha256:fb3464528ec7076f91195f0ff7835755aa023e281f1bb78a84782ce7a36b3705"
    )
    assert "build" not in service_def
