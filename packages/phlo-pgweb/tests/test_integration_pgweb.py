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
    assert plugin.metadata.name == "pgweb"
    assert plugin.service_definition["name"] == plugin.metadata.name


def test_pgweb_service_definition():
    """Test that service definition can be loaded."""
    from phlo_pgweb.plugin import PgwebServicePlugin

    plugin = PgwebServicePlugin()
    service_def = plugin.service_definition

    assert service_def["name"] == "pgweb"
    assert service_def["category"] == "admin"
    assert service_def["depends_on"] == ["postgres"]
    assert service_def["image"].startswith("sosedoff/pgweb:")
    assert service_def["compose"]["ports"] == ["${PGWEB_PORT:-8081}:8081"]
