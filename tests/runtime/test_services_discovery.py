"""Tests for plugin-backed service discovery.

Covers registry population from service.yaml definitions, rejection and
skipping of non-mapping definitions, stale-cache refresh, and load-failure
reporting.
"""

from pathlib import Path
from typing import Any, TypedDict, cast

import pytest

from phlo.plugins import PluginMetadata, ServicePlugin
from phlo.plugins.discovery import ServiceDefinition, ServiceDiscovery, get_global_registry
from phlo.plugins.discovery._service_discovery import ServiceDiscovery as CompatServiceDiscovery
from phlo.plugins.discovery.service_manifest import ServiceManifest, ServiceManifestError
from tests.helpers import DummyServicePlugin as _DummyServicePlugin

pytestmark = pytest.mark.core_regression


class DiscoverySignal(TypedDict):
    """Captured service discovery observability event."""

    level: str
    event: str
    fields: dict[str, object]


def test_compat_service_discovery_is_canonical_class() -> None:
    """Private compatibility module should not define a divergent class."""
    assert CompatServiceDiscovery is ServiceDiscovery


def _write_service_yaml(root: Path, folder: str, name: str) -> None:
    """Write a minimal service definition file for discovery tests."""
    service_file = root / folder / "service.yaml"
    service_file.parent.mkdir(parents=True, exist_ok=True)
    service_file.write_text(
        f"name: {name}\ndescription: {name} service\ncategory: core\n",
        encoding="utf-8",
    )


class DummyServicePlugin(_DummyServicePlugin):
    """Canonical valid service plugin fixture for this module."""

    def __init__(self) -> None:
        super().__init__(
            service_definition={
                "name": "dummy_service",
                "description": "Dummy service",
                "category": "core",
                "default": True,
                "compose": {"image": "dummy:latest"},
            }
        )


class NonMappingServicePlugin(ServicePlugin):
    """Provide an invalid non-mapping service definition for regression tests."""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="bad_service", version="1.0.0")

    @property
    def service_definition(self) -> dict[str, Any]:
        return cast(dict[str, Any], ["not", "a", "mapping"])


@pytest.fixture
def clean_registry():
    """Clear the global registry before and after each test."""
    registry = get_global_registry()
    registry.clear()
    yield registry
    registry.clear()


@pytest.fixture
def service_discovery_signals(monkeypatch: pytest.MonkeyPatch) -> list[DiscoverySignal]:
    """Capture service discovery observability events emitted via log_event."""
    signals: list[DiscoverySignal] = []

    def _capture_signal(
        _logger: object,
        level: str,
        event: str,
        **fields: object,
    ) -> None:
        signals.append({"level": level, "event": event, "fields": fields})

    monkeypatch.setattr("phlo.plugins.discovery.services.log_event", _capture_signal)
    return signals


def test_service_discovery_includes_plugins(
    clean_registry: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Ensure discovered services include registered service plugins."""
    registry = get_global_registry()
    registry.register("service", DummyServicePlugin(), replace=True)

    monkeypatch.setattr(
        "phlo.plugins.discovery._service_loading.discover_plugins",
        lambda plugin_type, auto_register: None,
    )

    discovery = ServiceDiscovery(services_dir=tmp_path)
    services = discovery.discover()

    assert "dummy_service" in services
    assert services["dummy_service"].category == "core"


def test_service_discovery_raises_for_non_mapping_plugin_service_definitions(
    clean_registry: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    service_discovery_signals: list[DiscoverySignal],
) -> None:
    """A plugin returning non-mapping service data raises a contextual manifest error."""
    registry = get_global_registry()
    registry.register("service", DummyServicePlugin(), replace=True)
    registry.register("service", NonMappingServicePlugin(), replace=True)
    monkeypatch.setattr(
        "phlo.plugins.discovery.service_manifest.discover_plugins",
        lambda plugin_type="service", auto_register=True: None,
    )

    with pytest.raises(ServiceManifestError) as exc:
        ServiceDiscovery(services_dir=tmp_path).discover()

    assert "invalid plugin service definition" in str(exc.value)
    assert "service=bad_service" in str(exc.value)
    assert any(
        signal["event"] == "service_discovery_manifest_load_failed"
        and signal["fields"]["error_type"] == "ServiceManifestError"
        and "bad_service" in str(signal["fields"]["error"])
        for signal in service_discovery_signals
    )


def test_service_loading_helper_skips_non_mapping_plugin_service_definitions(
    clean_registry: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compatibility helper also skips invalid plugin service payloads locally."""
    from phlo.plugins.discovery import _service_loading

    registry = get_global_registry()
    registry.register("service", DummyServicePlugin(), replace=True)
    registry.register("service", NonMappingServicePlugin(), replace=True)
    events: list[DiscoverySignal] = []
    services: dict[str, ServiceDefinition] = {}
    monkeypatch.setattr(
        _service_loading, "discover_plugins", lambda plugin_type, auto_register: None
    )
    monkeypatch.setattr(_service_loading, "resolve_plugin_source_path", lambda _plugin: None)
    monkeypatch.setattr(
        _service_loading,
        "log_event",
        lambda logger, level, event, **fields: events.append(
            {"level": level, "event": event, "fields": fields}
        ),
    )

    loaded_count = _service_loading.load_plugin_services(services)

    assert loaded_count >= 1
    assert "dummy_service" in services
    assert any(
        signal["level"] == "warning"
        and signal["event"] == "service_plugin_definition_invalid"
        and signal["fields"]["plugin_name"] == "bad_service"
        and "Service definition must be a mapping" in str(signal["fields"]["error"])
        for signal in events
    )


def test_service_discovery_refresh_reloads_stale_cache(
    clean_registry: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify cached discovery remains stale until explicit refresh."""
    monkeypatch.setattr(
        "phlo.plugins.discovery.service_manifest.discover_plugins",
        lambda plugin_type="service", auto_register=True: None,
    )
    _write_service_yaml(tmp_path, "alpha", "alpha")

    discovery = ServiceDiscovery(services_dir=tmp_path)
    first = discovery.discover()
    assert set(first) == {"alpha"}

    _write_service_yaml(tmp_path, "beta", "beta")

    cached = discovery.discover()
    assert cached is first
    assert set(cached) == {"alpha"}

    refreshed = discovery.discover(refresh=True)
    assert refreshed is not first
    assert set(refreshed) == {"alpha", "beta"}


def test_service_discovery_delegates_loading_behind_cache(
    clean_registry: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ServiceDiscovery owns caching while loading helpers own mutation details."""
    plugin_loads = 0
    directory_loads = 0

    def _resolve_plugin_manifests(self) -> list[ServiceManifest]:
        nonlocal plugin_loads
        plugin_loads += 1
        return [
            ServiceManifest(
                ServiceDefinition(
                    name="plugin",
                    description="Plugin service",
                    category="core",
                )
            )
        ]

    def _resolve_directory_manifests(self) -> list[ServiceManifest]:
        nonlocal directory_loads
        directory_loads += 1
        return [
            ServiceManifest(
                ServiceDefinition(
                    name="file",
                    description="File service",
                    category="core",
                )
            )
        ]

    monkeypatch.setattr(
        "phlo.plugins.discovery.service_manifest.ServiceManifestResolver.resolve_plugin_manifests",
        _resolve_plugin_manifests,
    )
    monkeypatch.setattr(
        "phlo.plugins.discovery.service_manifest.ServiceManifestResolver.resolve_directory_manifests",
        _resolve_directory_manifests,
    )

    discovery = ServiceDiscovery()
    first = discovery.discover()
    cached = discovery.discover()
    refreshed = discovery.refresh()

    assert cached is first
    assert set(refreshed) == {"plugin", "file"}
    assert plugin_loads == 2
    assert directory_loads == 2


def test_service_discovery_reports_manifest_load_failures(
    monkeypatch: pytest.MonkeyPatch,
    service_discovery_signals: list[DiscoverySignal],
) -> None:
    """Resolver errors are logged with context and re-raised."""

    def _raise_manifest_error(self) -> list[ServiceManifest]:
        raise ServiceManifestError("bad manifest", service_name="broken")

    monkeypatch.setattr(
        "phlo.plugins.discovery.service_manifest.ServiceManifestResolver.resolve_plugin_manifests",
        _raise_manifest_error,
    )

    with pytest.raises(ServiceManifestError, match="bad manifest: service=broken"):
        ServiceDiscovery().discover()

    assert any(
        signal["event"] == "service_discovery_manifest_load_failed"
        and signal["fields"]["error_type"] == "ServiceManifestError"
        and signal["fields"]["error"] == "bad manifest: service=broken"
        for signal in service_discovery_signals
    )


def test_service_discovery_resolver_counts_completion_signal(
    monkeypatch: pytest.MonkeyPatch,
    service_discovery_signals: list[DiscoverySignal],
) -> None:
    """Completion signals report plugin and directory manifest counts."""

    monkeypatch.setattr(
        "phlo.plugins.discovery.service_manifest.ServiceManifestResolver.resolve_plugin_manifests",
        lambda self: [
            ServiceManifest(
                ServiceDefinition(
                    name="plugin",
                    description="Plugin service",
                    category="core",
                )
            )
        ],
    )
    monkeypatch.setattr(
        "phlo.plugins.discovery.service_manifest.ServiceManifestResolver.resolve_directory_manifests",
        lambda self: [
            ServiceManifest(
                ServiceDefinition(
                    name="file",
                    description="File service",
                    category="core",
                )
            )
        ],
    )

    services = ServiceDiscovery().discover()

    assert set(services) == {"plugin", "file"}
    completed = next(
        signal
        for signal in service_discovery_signals
        if signal["event"] == "service_discovery_completed"
    )
    assert completed["fields"]["plugin_service_count"] == 1
    assert completed["fields"]["file_service_count"] == 1


def test_service_discovery_clear_cache_and_refresh_alias(
    clean_registry: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify cache invalidation API triggers rediscovery."""
    monkeypatch.setattr(
        "phlo.plugins.discovery.service_manifest.discover_plugins",
        lambda plugin_type="service", auto_register=True: None,
    )
    _write_service_yaml(tmp_path, "alpha", "alpha")

    discovery = ServiceDiscovery(services_dir=tmp_path)
    discovery.discover()

    _write_service_yaml(tmp_path, "beta", "beta")
    discovery.clear_cache()
    cleared_reload = discovery.discover()
    assert set(cleared_reload) == {"alpha", "beta"}

    _write_service_yaml(tmp_path, "gamma", "gamma")
    refreshed = discovery.refresh()
    assert set(refreshed) == {"alpha", "beta", "gamma"}


def test_service_discovery_emits_cache_refresh_observability_signals(
    clean_registry: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    service_discovery_signals: list[DiscoverySignal],
) -> None:
    """Cache and refresh flows emit structured, queryable discovery signals."""
    monkeypatch.setattr(
        "phlo.plugins.discovery.service_manifest.discover_plugins",
        lambda plugin_type="service", auto_register=True: None,
    )
    _write_service_yaml(tmp_path, "alpha", "alpha")

    discovery = ServiceDiscovery(services_dir=tmp_path)
    first = discovery.discover()
    assert set(first) == {"alpha"}

    cached = discovery.discover()
    assert cached is first

    _write_service_yaml(tmp_path, "beta", "beta")
    refreshed = discovery.discover(refresh=True)
    assert set(refreshed) == {"alpha", "beta"}

    events = [signal["event"] for signal in service_discovery_signals]
    assert "service_discovery_cache_miss" in events
    assert "service_discovery_cache_hit" in events
    assert "service_discovery_refresh_requested" in events
    assert "service_discovery_cache_cleared" in events
    assert events.count("service_discovery_completed") == 2

    cache_hit = next(
        signal
        for signal in service_discovery_signals
        if signal["event"] == "service_discovery_cache_hit"
    )
    assert cache_hit["fields"]["service_count"] == 1
    assert cache_hit["fields"]["services_dir"] == str(tmp_path)

    refresh_requested = next(
        signal
        for signal in service_discovery_signals
        if signal["event"] == "service_discovery_refresh_requested"
    )
    assert refresh_requested["fields"]["cached_service_count"] == 1
    assert refresh_requested["fields"]["cache_loaded"] is True

    completed = [
        signal
        for signal in service_discovery_signals
        if signal["event"] == "service_discovery_completed"
    ]
    assert completed[0]["fields"]["plugin_service_count"] == 0
    assert completed[0]["fields"]["file_service_count"] == 1
    assert completed[1]["fields"]["file_service_count"] == 2


def test_service_discovery_filters_services_by_profile(
    clean_registry: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Profile lookup returns only matching discovered service definitions."""
    monkeypatch.setattr(
        "phlo.plugins.discovery.service_manifest.discover_plugins",
        lambda plugin_type="service", auto_register=True: None,
    )
    _write_service_yaml(tmp_path, "core", "core-service")
    profile_service = tmp_path / "analytics" / "service.yaml"
    profile_service.parent.mkdir(parents=True)
    profile_service.write_text(
        "name: analytics-service\n"
        "description: analytics service\n"
        "category: core\n"
        "profile: analytics\n",
        encoding="utf-8",
    )

    discovery = ServiceDiscovery(services_dir=tmp_path)

    assert [service.name for service in discovery.get_services_by_profile("analytics")] == [
        "analytics-service"
    ]
    assert discovery.get_services_by_profile("missing") == []


def test_service_discovery_service_yaml_patterns() -> None:
    """Service YAML detection accepts only primary and companion filenames."""
    assert ServiceDiscovery._is_service_yaml("service.yaml") is True
    assert ServiceDiscovery._is_service_yaml("postgres-setup.yaml") is True
    assert ServiceDiscovery._is_service_yaml("worker-daemon.yaml") is True
    assert ServiceDiscovery._is_service_yaml("service.schema.yaml") is False
    assert ServiceDiscovery._is_service_yaml("values.yaml") is False


def test_service_discovery_loads_companion_service_files(
    tmp_path: Path,
    service_discovery_signals: list[DiscoverySignal],
) -> None:
    """Companion setup and daemon YAMLs are loaded from plugin package directories."""
    source_path = tmp_path / "plugin"
    source_path.mkdir()
    (source_path / "service.yaml").write_text(
        "name: main\ndescription: main service\n",
        encoding="utf-8",
    )
    (source_path / "worker-daemon.yaml").write_text(
        "name: worker\ndescription: worker service\nprofile: analytics\n",
        encoding="utf-8",
    )
    (source_path / "bootstrap-setup.yaml").write_text(
        "name: bootstrap\ndescription: bootstrap service\n",
        encoding="utf-8",
    )
    (source_path / "ignored.yaml").write_text(
        "name: ignored\ndescription: ignored service\n",
        encoding="utf-8",
    )
    (source_path / "broken-daemon.yaml").write_text(
        "description: missing name\n",
        encoding="utf-8",
    )

    discovery = ServiceDiscovery()

    assert discovery._load_companion_service_files(source_path) == 2
    assert set(discovery._services) == {"worker", "bootstrap"}
    assert discovery._services["worker"].profile == "analytics"
    assert any(
        signal["event"] == "service_discovery_companion_file_load_failed"
        and signal["fields"]["yaml_path"] == str(source_path / "broken-daemon.yaml")
        for signal in service_discovery_signals
    )


def test_service_discovery_raises_for_non_mapping_directory_service_files(
    clean_registry: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    service_discovery_signals: list[DiscoverySignal],
) -> None:
    """Non-mapping service YAML raises a contextual manifest error."""
    monkeypatch.setattr(
        "phlo.plugins.discovery.service_manifest.discover_plugins",
        lambda plugin_type="service", auto_register=True: None,
    )
    _write_service_yaml(tmp_path, "valid", "valid")
    broken = tmp_path / "broken" / "service.yaml"
    broken.parent.mkdir()
    broken.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    with pytest.raises(ServiceManifestError) as exc:
        ServiceDiscovery(services_dir=tmp_path).discover()

    assert "invalid service definition file" in str(exc.value)
    assert f"source={broken}" in str(exc.value)
    assert any(
        signal["event"] == "service_discovery_manifest_load_failed"
        and signal["fields"]["error_type"] == "ServiceManifestError"
        and str(broken) in str(signal["fields"]["error"])
        for signal in service_discovery_signals
    )


def test_service_discovery_skips_malformed_companion_service_files(
    tmp_path: Path,
    service_discovery_signals: list[DiscoverySignal],
) -> None:
    """Malformed companion YAML is logged and skipped without aborting discovery."""
    source_path = tmp_path / "plugin"
    source_path.mkdir()
    (source_path / "worker-daemon.yaml").write_text(
        "name: worker\ndescription: worker service\n",
        encoding="utf-8",
    )
    (source_path / "broken-daemon.yaml").write_text(
        "name: broken: [\n",
        encoding="utf-8",
    )

    discovery = ServiceDiscovery()

    assert discovery._load_companion_service_files(source_path) == 1
    assert set(discovery._services) == {"worker"}
    assert any(
        signal["event"] == "service_discovery_companion_file_load_failed"
        and signal["fields"]["yaml_path"] == str(source_path / "broken-daemon.yaml")
        and signal["fields"]["error_type"] in {"ParserError", "ScannerError"}
        for signal in service_discovery_signals
    )


def test_inline_service_creation() -> None:
    """Test creating a service from inline config."""
    inline_config = {
        "type": "inline",
        "image": "my-registry/api:latest",
        "ports": ["4000:4000", "4001:4001"],
        "environment": {"API_KEY": "secret", "DEBUG": "true"},
        "volumes": ["./data:/data"],
        "mem_limit": "1g",
        "depends_on": ["trino", "postgres"],
        "command": ["uvicorn", "main:app"],
        "description": "My custom API",
    }

    service = ServiceDefinition.from_inline("custom-api", inline_config)

    assert service.name == "custom-api"
    assert service.description == "My custom API"
    assert service.category == "custom"
    assert service.default is True
    assert service.image == "my-registry/api:latest"
    assert service.depends_on == ["trino", "postgres"]
    assert service.compose["ports"] == ["4000:4000", "4001:4001"]
    assert service.compose["environment"] == {"API_KEY": "secret", "DEBUG": "true"}
    assert service.compose["volumes"] == ["./data:/data"]
    assert service.compose["mem_limit"] == "1g"
    assert service.compose["command"] == ["uvicorn", "main:app"]


def test_inline_service_minimal() -> None:
    """Test inline service with minimal config."""
    minimal_config = {
        "type": "inline",
        "image": "nginx:latest",
    }

    service = ServiceDefinition.from_inline("web-server", minimal_config)

    assert service.name == "web-server"
    assert service.description == "Custom service: web-server"
    assert service.category == "custom"
    assert service.image == "nginx:latest"
    assert service.depends_on == []
    assert service.compose == {}


def test_inline_service_with_build() -> None:
    """Test inline service using build instead of image."""
    build_config = {
        "type": "inline",
        "build": {"context": "./my-app", "dockerfile": "Dockerfile.dev"},
        "ports": ["3000:3000"],
    }

    service = ServiceDefinition.from_inline("my-app", build_config)

    assert service.name == "my-app"
    assert service.build == {"context": "./my-app", "dockerfile": "Dockerfile.dev"}
    assert service.image is None
    assert service.compose["ports"] == ["3000:3000"]
