"""Discover and cache service definitions from plugins and optional directories.

ServiceDiscovery loads definitions from installed service plugins and,
when a services_dir is given, extra service.yaml files; results are
cached per instance until refresh=True. Dependency cycles are rejected
via shared cycle detection, and manifest errors surface as
ServiceManifestError.

Imported by phlo.plugins.discovery (its public facade) and by the CLI services
command utilities.
"""

from pathlib import Path
from typing import Any, cast

import yaml

from phlo.logging import get_logger, log_event
from phlo.plugins.base.service import ServicePlugin
from phlo.plugins.discovery._service_cycles import find_cycles as _find_cycles_impl
from phlo.plugins.discovery._service_definition import ServiceDefinition
from phlo.plugins.discovery._service_dependency_resolution import resolve_service_dependencies
from phlo.plugins.discovery.registry import get_global_registry
from phlo.plugins.discovery.service_manifest import ServiceManifestError, ServiceManifestResolver

logger = get_logger(__name__)


def _services_dir_label(services_dir: Path | None) -> str:
    """Normalize the services directory path for structured observability fields."""
    return str(services_dir) if services_dir else "<plugins-only>"


def _emit_service_discovery_signal(
    *,
    event_name: str,
    level: str,
    services_dir: Path | None,
    **fields: Any,
) -> None:
    """Emit a structured observability event for service discovery flows."""
    log_event(
        logger,
        level,
        event_name,
        services_dir=_services_dir_label(services_dir),
        **fields,
    )


def discover_plugins(plugin_type: str = "service", auto_register: bool = True):
    """Compatibility wrapper for tests and service discovery call sites."""
    import phlo.plugins.discovery._service_loading as _service_loading

    return _service_loading.discover_plugins(plugin_type=plugin_type, auto_register=auto_register)


def _find_cycles(nodes: set[str], graph: dict[str, set[str]]) -> list[list[str]]:
    """Compatibility wrapper around shared cycle-detection utilities."""
    return _find_cycles_impl(nodes, graph)


class ServiceDiscovery:
    """Discovers and manages service definitions."""

    def __init__(self, services_dir: Path | None = None):
        """Initialize with an optional directory of additional service.yaml files.

        When services_dir is None, services come exclusively from installed
        service plugins.
        """
        self.services_dir = services_dir
        self._services: dict[str, ServiceDefinition] = {}
        self._loaded = False

    def discover(self, refresh: bool = False) -> dict[str, ServiceDefinition]:
        """Discover all service definitions, cached unless ``refresh`` is True."""
        if refresh:
            _emit_service_discovery_signal(
                event_name="service_discovery_refresh_requested",
                level="info",
                services_dir=self.services_dir,
                cached_service_count=len(self._services),
                cache_loaded=self._loaded,
            )
            self.clear_cache()

        if self._loaded:
            _emit_service_discovery_signal(
                event_name="service_discovery_cache_hit",
                level="debug",
                services_dir=self.services_dir,
                service_count=len(self._services),
            )
            return self._services

        _emit_service_discovery_signal(
            event_name="service_discovery_cache_miss",
            level="debug",
            services_dir=self.services_dir,
            refresh=refresh,
        )

        self._services = {}
        resolver = ServiceManifestResolver(services_dir=self.services_dir)
        try:
            plugin_manifests = resolver.resolve_plugin_manifests()
            directory_manifests = resolver.resolve_directory_manifests()
        except ServiceManifestError as exc:
            _emit_service_discovery_signal(
                event_name="service_discovery_manifest_load_failed",
                level="warning",
                services_dir=self.services_dir,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise

        # Plugin manifests are inserted first, so setdefault makes a
        # plugin-provided definition win over a same-named file from
        # services_dir. Directory files can only fill gaps.
        for manifest in plugin_manifests:
            self._services.setdefault(manifest.name, manifest.definition)
        for manifest in directory_manifests:
            self._services.setdefault(manifest.name, manifest.definition)

        plugin_service_count = len(plugin_manifests)
        file_service_count = len(directory_manifests)

        self._loaded = True
        _emit_service_discovery_signal(
            event_name="service_discovery_completed",
            level="info",
            services_dir=self.services_dir,
            service_count=len(self._services),
            plugin_service_count=plugin_service_count,
            file_service_count=file_service_count,
        )
        return self._services

    def clear_cache(self) -> None:
        """Clear cached discovery state.

        The next :meth:`discover` call will perform a full rediscovery.
        """
        registry = get_global_registry()
        for service_name in list(registry.list("service")):
            registry.remove("service", service_name)

        cached_service_count = len(self._services)
        was_loaded = self._loaded
        self._services = {}
        self._loaded = False
        _emit_service_discovery_signal(
            event_name="service_discovery_cache_cleared",
            level="info",
            services_dir=self.services_dir,
            cached_service_count=cached_service_count,
            cache_was_loaded=was_loaded,
        )

    def refresh(self) -> dict[str, ServiceDefinition]:
        """Force rediscovery and return refreshed service definitions."""
        return self.discover(refresh=True)

    def _load_service_plugins(self) -> int:
        """Load services from installed plugins."""
        import phlo.plugins.discovery._service_loading as _service_loading

        loaded_count = 0
        registry = get_global_registry()
        discover_plugins(plugin_type="service", auto_register=True)

        for name in registry.list("service"):
            plugin = cast(ServicePlugin | None, registry.get("service", name))
            if not plugin:
                continue
            if name in self._services:
                logger.debug("plugin_service_skipped_core_exists", service_name=name)
                continue

            service_definition = plugin.service_definition
            source_path = _service_loading.resolve_plugin_source_path(plugin)
            try:
                service = ServiceDefinition.from_dict(service_definition, source_path)
                self._services[service.name] = service
                loaded_count += 1
                loaded_count += self._load_companion_service_files(source_path)
            except (KeyError, ValueError) as exc:
                _emit_service_discovery_signal(
                    event_name="service_discovery_plugin_load_failed",
                    level="warning",
                    services_dir=self.services_dir,
                    plugin_name=name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
        return loaded_count

    @staticmethod
    def _is_service_yaml(filename: str) -> bool:
        """Check if filename is a recognized service YAML pattern."""
        return filename == "service.yaml" or filename.endswith(("-setup.yaml", "-daemon.yaml"))

    def _load_companion_service_files(self, source_path: Path | None) -> int:
        """Load companion service YAMLs (e.g., *-setup.yaml, *-daemon.yaml) from a package."""
        if not source_path or not source_path.exists():
            return 0

        loaded_count = 0
        for yaml_path in source_path.rglob("*.yaml"):
            filename = yaml_path.name
            if filename == "service.yaml":
                continue
            if not filename.endswith(("-setup.yaml", "-daemon.yaml")):
                continue

            try:
                service = ServiceDefinition.from_yaml(yaml_path)
                if service.name in self._services:
                    continue
                self._services[service.name] = service
                loaded_count += 1
            except (yaml.YAMLError, KeyError, ValueError) as exc:
                _emit_service_discovery_signal(
                    event_name="service_discovery_companion_file_load_failed",
                    level="warning",
                    services_dir=self.services_dir,
                    yaml_path=str(yaml_path),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
        return loaded_count

    def get_service(self, name: str) -> ServiceDefinition | None:
        """Get a service definition by name."""
        self.discover()
        return self._services.get(name)

    def get_default_services(
        self, disabled_services: set[str] | None = None
    ) -> list[ServiceDefinition]:
        """Get all services marked as default, excluding disabled ones."""
        self.discover()
        disabled = disabled_services or set()
        return [s for s in self._services.values() if s.default and s.name not in disabled]

    def get_services_by_profile(self, profile: str) -> list[ServiceDefinition]:
        """Get all services in a specific profile."""
        self.discover()
        return [s for s in self._services.values() if s.profile == profile]

    def get_services_by_category(self, category: str) -> list[ServiceDefinition]:
        """Get all services in a specific category."""
        self.discover()
        return [s for s in self._services.values() if s.category == category]

    def get_available_profiles(self) -> set[str]:
        """Get all available profile names."""
        self.discover()
        return {s.profile for s in self._services.values() if s.profile}

    def resolve_dependencies(self, services: list[ServiceDefinition]) -> list[ServiceDefinition]:
        """Sort services topologically so dependencies come first.

        Raises ValueError when circular dependencies are detected.
        """
        self.discover()

        return resolve_service_dependencies(services)

    def list_all(self) -> list[dict[str, Any]]:
        """List all services as metadata dictionaries."""
        self.discover()
        return [
            {
                "name": s.name,
                "description": s.description,
                "category": s.category,
                "default": s.default,
                "profile": s.profile,
                "depends_on": s.depends_on,
            }
            for s in sorted(self._services.values(), key=lambda x: (x.category, x.name))
        ]
