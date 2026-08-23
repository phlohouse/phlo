"""Integration tests for phlo-pgweb.

Smoke-checks plugin instantiation and service-definition loading; imports
are deferred into each test so the suite runs without the package installed
until these integration tests execute.
"""

import pytest

pytestmark = pytest.mark.integration


def test_pgweb_plugin_initializes():
    """Test that PgWeb plugin can be instantiated."""
    from phlo_pgweb.plugin import PgwebServicePlugin

    plugin = PgwebServicePlugin()
    assert plugin is not None
    assert plugin.metadata.name == "pgweb"


def test_pgweb_service_definition():
    """Test that service definition can be loaded."""
    from phlo_pgweb.plugin import PgwebServicePlugin

    plugin = PgwebServicePlugin()
    service_def = plugin.service_definition

    assert isinstance(service_def, dict)
