"""Shared test helpers to avoid duplication across test files.

Centralizes plugin metadata and service-plugin construction used by multiple
suites.
"""

from __future__ import annotations

from typing import Any

from phlo.plugins import (
    PluginMetadata,
    QualityCheckPlugin,
    ServicePlugin,
    SourceConnectorPlugin,
    TransformationPlugin,
)
from phlo.plugins.discovery import ServiceDefinition


def reset_capability_test_state() -> None:
    """Clear global capability and config caches between stateful tests."""
    from phlo.capabilities import clear_all_capabilities
    from phlo.config import _get_config
    from phlo.infrastructure import clear_config_cache

    _get_config.cache_clear()
    clear_config_cache()
    clear_all_capabilities()


def _service(
    name: str,
    *,
    default: bool = False,
    profile: str | None = None,
    category: str = "core",
    depends_on: list[str] | None = None,
) -> ServiceDefinition:
    """Build a service definition test fixture."""
    return ServiceDefinition(
        name=name,
        description=f"{name} service",
        category=category,
        default=default,
        profile=profile,
        depends_on=depends_on or [],
    )


class FakeDiscovery:
    """Minimal service discovery stub for tests."""

    def __init__(
        self,
        services: dict[str, ServiceDefinition] | None = None,
        *,
        default_names: tuple[str, ...] = (),
    ):
        self._services = services or {}
        self._default_names = default_names

    def discover(self) -> dict[str, ServiceDefinition]:
        return self._services

    def get_service(self, _name: str) -> ServiceDefinition | None:
        return self._services.get(_name)

    def resolve_dependencies(self, services: list[ServiceDefinition]) -> list[ServiceDefinition]:
        return services

    def get_default_services(self, disabled_services=None) -> list[ServiceDefinition]:
        disabled = set(disabled_services or [])
        return [self._services[name] for name in self._default_names if name not in disabled]

    def get_available_profiles(self) -> set[str]:
        return {svc.profile for svc in self._services.values() if svc.profile}

    def get_services_by_profile(self, profile: str) -> list[ServiceDefinition]:
        return [svc for svc in self._services.values() if svc.profile == profile]


class DummySourcePlugin(SourceConnectorPlugin):
    """Minimal source connector plugin for registry and CLI tests."""

    def __init__(self, name: str = "dummy_source", description: str = "", author: str = "") -> None:
        self._metadata = PluginMetadata(
            name=name, version="1.0.0", description=description, author=author
        )

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata

    def fetch_data(self, config):
        yield {"id": 1, "value": "test"}


class DummyQualityPlugin(QualityCheckPlugin):
    """Minimal quality check plugin for registry and CLI tests."""

    def __init__(
        self, name: str = "dummy_quality", description: str = "", author: str = ""
    ) -> None:
        self._metadata = PluginMetadata(
            name=name, version="1.0.0", description=description, author=author
        )

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata

    def create_check(self, **kwargs):
        return None


class DummyTransformPlugin(TransformationPlugin):
    """Minimal transformation plugin for registry and CLI tests."""

    def __init__(
        self, name: str = "dummy_transform", description: str = "", author: str = ""
    ) -> None:
        self._metadata = PluginMetadata(
            name=name, version="1.0.0", description=description, author=author
        )

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata

    def transform(self, df, config):
        return df


class DummyServicePlugin(ServicePlugin):
    """Minimal service plugin for registry, CLI, and discovery tests."""

    def __init__(
        self,
        name: str = "dummy_service",
        description: str = "",
        author: str = "",
        service_definition: dict[str, Any] | None = None,
    ) -> None:
        self._metadata = PluginMetadata(
            name=name, version="1.0.0", description=description, author=author
        )
        self._service_definition = service_definition or {
            "category": "core",
            "compose": {"image": "dummy:latest"},
        }

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata

    @property
    def service_definition(self) -> dict[str, Any]:
        return self._service_definition


class RecordingBus:
    """Test bus that records emitted events."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)
