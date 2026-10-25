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
    """Test that the service definition pins the upstream image."""
    from phlo_hasura.plugin import HasuraServicePlugin

    plugin = HasuraServicePlugin()
    service_def = plugin.service_definition

    assert service_def["name"] == "hasura"
    assert service_def["image"] == (
        "${HASURA_IMAGE:-hasura/graphql-engine:v2.49.5@"
        "sha256:a9f427a9078b75c5f43ea40abd4ba4e426f45777f862eff7265f411a5ac96086}"
    )
    assert "build" not in service_def
