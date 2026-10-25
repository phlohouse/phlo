"""Integration tests for phlo-core-plugins.

Per TEST_STRATEGY.md:
- Plugin Lifecycle: Registration, conflict resolution, metadata validation
"""

import pytest

pytestmark = pytest.mark.integration


def test_service_plugin_requires_the_documented_interface() -> None:
    """The service-plugin base class cannot supply metadata or a definition itself."""
    from phlo.plugins import ServicePlugin

    with pytest.raises(TypeError):
        ServicePlugin()

    class ConcretePlugin(ServicePlugin):
        """Minimal implementation proving the base class accepts a real subclass."""

        @property
        def metadata(self):
            """Return test plugin metadata."""
            from phlo.plugins.base import PluginMetadata

            return PluginMetadata("test", "1.0.0", "test")

        @property
        def service_definition(self):
            """Return a minimal service definition."""
            return {"services": {}}

    plugin = ConcretePlugin()
    assert plugin.metadata.name == "test"
    assert plugin.service_definition == {"services": {}}
