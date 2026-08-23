"""Integration tests for phlo-hasura.

Runs under the integration marker: exercises real plugin initialization and
the Hasura service definition end-to-end instead of with mocks.
"""

import pytest

pytestmark = pytest.mark.integration


def test_hasura_plugin_initializes():
    """Test that Hasura plugin can be instantiated."""
    from phlo_hasura.plugin import HasuraServicePlugin

    plugin = HasuraServicePlugin()
    assert plugin is not None
    assert plugin.metadata.name == "hasura"


def test_hasura_service_definition():
    """Test that service definition can be loaded."""
    from phlo_hasura.plugin import HasuraServicePlugin

    plugin = HasuraServicePlugin()
    service_def = plugin.service_definition

    assert isinstance(service_def, dict)
