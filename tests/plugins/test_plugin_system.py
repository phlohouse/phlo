"""
Tests for plugin system (Spec 6).

Tests the complete plugin lifecycle:
- Discovery and registration
- Plugin metadata
- Plugin validation
- Whitelisting/blacklisting
"""

from types import SimpleNamespace

import pytest

from phlo.plugins import (
    PluginMetadata,
    SourceConnectorPlugin,
    TransformationPlugin,
    discover_plugins,
    get_plugin,
    get_plugin_info,
    list_plugins,
    validate_plugins,
)
from phlo.plugins.discovery import _plugin_auto_discovery as plugin_auto_discovery
from phlo.plugins.discovery import get_global_registry
from tests.helpers import (
    DummyQualityPlugin as _DummyQualityPlugin,
)
from tests.helpers import (
    DummyServicePlugin as _DummyServicePlugin,
)
from tests.helpers import (
    DummySourcePlugin as _DummySourcePlugin,
)
from tests.helpers import (
    DummyTransformPlugin as _DummyTransformPlugin,
)

pytestmark = pytest.mark.core_regression


class DummySourcePlugin(_DummySourcePlugin):
    """Canonical source plugin fixture for this module."""

    def __init__(self) -> None:
        super().__init__("test_source", "Test source plugin", author="Test")


class DummyQualityPlugin(_DummyQualityPlugin):
    """Canonical quality plugin fixture for this module."""

    def __init__(self) -> None:
        super().__init__("test_quality", "Test quality plugin")


class DummyTransformPlugin(_DummyTransformPlugin):
    """Canonical transform plugin fixture for this module."""

    def __init__(self) -> None:
        super().__init__("test_transform", "Test transform plugin")


class DummyServicePlugin(_DummyServicePlugin):
    """Canonical service plugin fixture for this module."""

    def __init__(self) -> None:
        super().__init__(
            "test_service",
            "Test service plugin",
            service_definition={
                "category": "core",
                "compose": {"image": "test-service:latest"},
            },
        )


@pytest.fixture
def clean_registry():
    """Clear and restore registry for each test."""
    registry = get_global_registry()
    registry.clear()
    yield registry
    registry.clear()


class TestPluginRegistration:
    """Test plugin registration."""

    def test_register_source_connector(self, clean_registry):
        """Test registering a source connector."""
        plugin = DummySourcePlugin()
        clean_registry.register("source_connector", plugin)

        retrieved = clean_registry.get("source_connector", "test_source")
        assert retrieved is plugin

    def test_register_quality_check(self, clean_registry):
        """Test registering a quality check."""
        plugin = DummyQualityPlugin()
        clean_registry.register("quality_check", plugin)

        retrieved = clean_registry.get("quality_check", "test_quality")
        assert retrieved is plugin

    def test_register_transformation(self, clean_registry):
        """Test registering a transformation."""
        plugin = DummyTransformPlugin()
        clean_registry.register("transformation", plugin)

        retrieved = clean_registry.get("transformation", "test_transform")
        assert retrieved is plugin

    def test_register_service(self, clean_registry):
        """Test registering a service."""
        plugin = DummyServicePlugin()
        clean_registry.register("service", plugin)

        retrieved = clean_registry.get("service", "test_service")
        assert retrieved is plugin

    def test_typed_accessors_match_generic_registry_api(self, clean_registry):
        """Typed registry helpers should stay aligned with generic register/get/list."""
        plugin = DummyServicePlugin()
        clean_registry.register("service", plugin)

        assert clean_registry.get("service", "test_service") is plugin
        assert clean_registry.get("service", "test_service") is plugin
        assert clean_registry.list("service") == ["test_service"]
        assert clean_registry.list("service") == ["test_service"]

    def test_duplicate_registration_raises_error(self, clean_registry):
        """Test duplicate registration raises error."""
        plugin = DummySourcePlugin()
        clean_registry.register("source_connector", plugin)

        with pytest.raises(ValueError):
            clean_registry.register("source_connector", plugin)

    def test_duplicate_registration_with_replace(self, clean_registry):
        """Test duplicate registration with replace=True."""
        plugin1 = DummySourcePlugin()
        clean_registry.register("source_connector", plugin1)

        plugin2 = DummySourcePlugin()
        clean_registry.register("source_connector", plugin2, replace=True)

        retrieved = clean_registry.get("source_connector", "test_source")
        assert retrieved is plugin2


class TestPluginMetadata:
    """Test plugin metadata retrieval."""

    def test_get_source_metadata(self, clean_registry):
        """Test getting source metadata."""
        plugin = DummySourcePlugin()
        clean_registry.register("source_connector", plugin)

        metadata = clean_registry.get_plugin_metadata("source_connector", "test_source")
        assert metadata["name"] == "test_source"
        assert metadata["version"] == "1.0.0"
        assert metadata["author"] == "Test"

    def test_get_quality_metadata(self, clean_registry):
        """Test getting quality metadata."""
        plugin = DummyQualityPlugin()
        clean_registry.register("quality_check", plugin)

        metadata = clean_registry.get_plugin_metadata("quality_check", "test_quality")
        assert metadata["name"] == "test_quality"

    def test_get_service_metadata(self, clean_registry):
        """Test getting service metadata."""
        plugin = DummyServicePlugin()
        clean_registry.register("service", plugin)

        metadata = clean_registry.get_plugin_metadata("service", "test_service")
        assert metadata["name"] == "test_service"

    def test_missing_plugin_returns_none(self, clean_registry):
        """Test getting metadata for non-existent plugin."""
        metadata = clean_registry.get_plugin_metadata("source_connector", "nonexistent")
        assert metadata is None


class TestPluginValidation:
    """Test plugin validation."""

    def test_validate_valid_plugin(self, clean_registry):
        """Test validating a valid plugin."""
        plugin = DummySourcePlugin()
        assert clean_registry.validate_plugin(plugin) is True
        service_plugin = DummyServicePlugin()
        assert clean_registry.validate_plugin(service_plugin) is True

    def test_validate_missing_metadata(self, clean_registry):
        """Test validation fails for missing metadata."""

        class BadPlugin:
            """Plugin-like object missing required metadata and methods."""

        assert clean_registry.validate_plugin(BadPlugin()) is False

    def test_validate_missing_required_method(self, clean_registry):
        """Test validation fails for missing required method."""
        # Note: Abstract methods are enforced at instantiation time,
        # so we test with a mock that has the structure but missing callable

        class BrokenSource(SourceConnectorPlugin):
            """Source plugin with fetch method intentionally overridden in test."""

            @property
            def metadata(self):
                """Return minimal metadata for validation tests."""
                return PluginMetadata(name="broken", version="1.0.0")

            def fetch_data(self, config):
                """Return a non-generator payload used in validation tests."""
                return []

        plugin = BrokenSource()
        # Override to make it not callable
        plugin.fetch_data = None  # type: ignore
        assert clean_registry.validate_plugin(plugin) is False


class TestPluginListing:
    """Test plugin listing."""

    def test_list_empty_registry(self, clean_registry):
        """Test listing from empty registry."""
        plugins = list_plugins()
        assert all(len(v) == 0 for v in plugins.values())

    def test_list_source_connectors(self, clean_registry):
        """Test listing source connectors."""
        plugin1 = DummySourcePlugin()

        class AnotherSourcePlugin(SourceConnectorPlugin):
            """Additional source connector used to test listing behavior."""

            @property
            def metadata(self) -> PluginMetadata:
                """Return metadata for the second source plugin."""
                return PluginMetadata(
                    name="test_source2",
                    version="1.0.0",
                    description="Test source plugin 2",
                )

            def fetch_data(self, config):
                """Yield one dummy source row."""
                yield {"id": 2, "value": "test2"}

        plugin2 = AnotherSourcePlugin()

        clean_registry.register("source_connector", plugin1)
        clean_registry.register("source_connector", plugin2)

        sources = clean_registry.list("source_connector")
        assert "test_source" in sources
        assert "test_source2" in sources

    def test_list_all_plugins(self, clean_registry):
        """Test listing all plugins."""
        clean_registry.register("source_connector", DummySourcePlugin())
        clean_registry.register("quality_check", DummyQualityPlugin())
        clean_registry.register("transformation", DummyTransformPlugin())
        clean_registry.register("service", DummyServicePlugin())

        all_plugins = clean_registry.list_all_plugins()
        assert "test_source" in all_plugins["source_connector"]
        assert "test_quality" in all_plugins["quality_check"]
        assert "test_transform" in all_plugins["transformation"]
        assert "test_service" in all_plugins["service"]


class TestPluginDiscovery:
    """Test plugin discovery (entry points)."""

    def test_discover_plugins_returns_dict(self):
        """Test discover_plugins returns correct structure."""
        result = discover_plugins(auto_register=False)

        assert isinstance(result, dict)
        assert "source_connector" in result
        assert "quality_check" in result
        assert "transformation" in result
        assert "service" in result

    def test_discover_single_type(self):
        """Test discovering single plugin type."""
        result = discover_plugins(plugin_type="source_connector", auto_register=False)

        assert "source_connector" in result
        # Other types might not be present
        assert isinstance(result["source_connector"], list)

    def test_discover_services_type(self):
        """Test discovering service plugins."""
        result = discover_plugins(plugin_type="service", auto_register=False)

        assert "service" in result
        assert isinstance(result["service"], list)

    def test_discover_with_validation(self):
        """Test discovery validates plugins."""
        result = discover_plugins(auto_register=False)

        # All discovered plugins should be valid
        for plugin_list in result.values():
            for plugin in plugin_list:
                registry = get_global_registry()
                assert registry.validate_plugin(plugin)

    @pytest.mark.parametrize(
        ("plugin_type", "expected_names"),
        [
            ("cli_command", {"alerts", "minio", "openmetadata", "sling"}),
            ("service", {"loki", "minio", "openmetadata"}),
            ("hook", {"alerting", "openmetadata"}),
            ("ingestion_provider", {"sling"}),
        ],
    )
    def test_workspace_entry_point_plugins_are_discoverable(
        self,
        plugin_type: str,
        expected_names: set[str],
    ) -> None:
        """Workspace plugins reported as dead should remain discoverable via entry points."""
        result = discover_plugins(
            plugin_type=plugin_type,
            auto_register=False,
            failure_level="debug",
        )

        discovered_names = {plugin.metadata.name for plugin in result[plugin_type]}
        assert expected_names <= discovered_names


class TestPluginAutoDiscoveryBootstrap:
    """Test import-time auto-discovery precedence."""

    def test_auto_discovery_enabled_from_settings(self, monkeypatch):
        """Enable auto-discovery when config is enabled and env override is absent."""
        monkeypatch.delenv("PHLO_NO_AUTO_DISCOVER", raising=False)
        monkeypatch.setattr(
            "phlo.plugins.discovery._plugin_auto_discovery.get_settings",
            lambda: SimpleNamespace(plugins_auto_discover=True),
        )
        assert plugin_auto_discovery.should_auto_discover() is True

    def test_auto_discovery_disabled_from_settings(self, monkeypatch):
        """Disable auto-discovery when config is disabled."""
        monkeypatch.delenv("PHLO_NO_AUTO_DISCOVER", raising=False)
        monkeypatch.setattr(
            "phlo.plugins.discovery._plugin_auto_discovery.get_settings",
            lambda: SimpleNamespace(plugins_auto_discover=False),
        )
        assert plugin_auto_discovery.should_auto_discover() is False

    def test_env_override_disables_auto_discovery(self, monkeypatch):
        """PHLO_NO_AUTO_DISCOVER overrides enabled settings."""
        monkeypatch.setenv("PHLO_NO_AUTO_DISCOVER", "1")
        monkeypatch.setattr(
            "phlo.plugins.discovery._plugin_auto_discovery.get_settings",
            lambda: SimpleNamespace(plugins_auto_discover=True),
        )
        assert plugin_auto_discovery.should_auto_discover() is False

    def test_env_falsy_value_keeps_auto_discovery_enabled(self, monkeypatch):
        """Falsy env values do not disable auto-discovery."""
        monkeypatch.setenv("PHLO_NO_AUTO_DISCOVER", "0")
        monkeypatch.setattr(
            "phlo.plugins.discovery._plugin_auto_discovery.get_settings",
            lambda: SimpleNamespace(plugins_auto_discover=True),
        )
        assert plugin_auto_discovery.should_auto_discover() is True

    def test_auto_discovery_warns_on_failures_in_open_mode(self, monkeypatch):
        """Open-mode auto-discovery keeps the existing warning-only behavior."""
        calls: list[dict[str, object]] = []
        warnings: list[str] = []

        def fake_discover_plugins(**kwargs):
            calls.append(kwargs)
            raise RuntimeError("boom")

        monkeypatch.setattr(plugin_auto_discovery, "_strict_auto_discovery_enabled", lambda: False)
        monkeypatch.setattr(plugin_auto_discovery, "discover_plugins", fake_discover_plugins)
        monkeypatch.setattr(
            plugin_auto_discovery.logger,
            "warning",
            lambda event, **kwargs: warnings.append(event),
        )

        plugin_auto_discovery.auto_discover()

        assert calls == [{"auto_register": True, "strict": False}]
        assert warnings == ["plugin_auto_discover_failed"]

    def test_auto_discovery_raises_on_failures_in_regulated_mode(self, monkeypatch):
        """Regulated auto-discovery fails fast instead of hiding missing capabilities."""

        def fake_discover_plugins(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(plugin_auto_discovery, "_strict_auto_discovery_enabled", lambda: True)
        monkeypatch.setattr(plugin_auto_discovery, "discover_plugins", fake_discover_plugins)

        with pytest.raises(RuntimeError, match="boom"):
            plugin_auto_discovery.auto_discover()


class TestPluginIntegration:
    """Integration tests for plugin system."""

    def test_register_and_retrieve(self, clean_registry):
        """Test registering and retrieving plugins."""
        source = DummySourcePlugin()
        quality = DummyQualityPlugin()
        transform = DummyTransformPlugin()
        service = DummyServicePlugin()

        clean_registry.register("source_connector", source)
        clean_registry.register("quality_check", quality)
        clean_registry.register("transformation", transform)
        clean_registry.register("service", service)

        assert get_plugin("source_connector", "test_source") is source
        assert get_plugin("quality_check", "test_quality") is quality
        assert get_plugin("transformation", "test_transform") is transform
        assert get_plugin("service", "test_service") is service

    def test_get_plugin_info(self, clean_registry):
        """Test getting plugin information."""
        plugin = DummySourcePlugin()
        clean_registry.register("source_connector", plugin)

        info = get_plugin_info("source_connector", "test_source")
        assert info is not None
        assert info["name"] == "test_source"
        assert info["version"] == "1.0.0"

    def test_validate_plugins(self, clean_registry):
        """Test validating all plugins."""
        clean_registry.register("source_connector", DummySourcePlugin())
        clean_registry.register("quality_check", DummyQualityPlugin())

        result = validate_plugins()

        assert "valid" in result
        assert "invalid" in result
        assert len(result["valid"]) == 2
        assert len(result["invalid"]) == 0

    def test_register_get_list_flow(self, clean_registry):
        """Test complete workflow of register, get, and list."""
        plugin = DummySourcePlugin()
        clean_registry.register("source_connector", plugin)

        # List
        plugins = clean_registry.list("source_connector")
        assert "test_source" in plugins

        # Get
        retrieved = clean_registry.get("source_connector", "test_source")
        assert retrieved is plugin

        # Get info
        info = clean_registry.get_plugin_metadata("source_connector", "test_source")
        assert info["name"] == "test_source"


def test_transformation_plugin_subclassing_warns_deprecation():
    """Subclassing the deprecated transformation base warns but still works."""
    with pytest.warns(DeprecationWarning, match="TransformationPlugin is deprecated"):

        class _DeprecatedTransform(TransformationPlugin):
            @property
            def metadata(self):
                return PluginMetadata(
                    name="deprecated_transform",
                    version="1.0.0",
                    description="Deprecation pin",
                )

            def transform(self, df, config):
                return df

    assert _DeprecatedTransform is not None
