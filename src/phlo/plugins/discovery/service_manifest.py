"""Service package manifest resolution primitives.

Resolves a service's primary manifest and picks up sibling *-setup.yaml /
*-daemon.yaml manifests beside the plugin source, skipping already-registered
names; dependency resolution layers on top of the resolved manifests.
Imported by phlo.plugins.discovery.services and the phlo CLI services commands
(phlo.cli.commands.services.utils) to resolve service manifests.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from phlo.plugins.discovery._plugin_loading import discover_plugins
from phlo.plugins.discovery._service_definition import ServiceDefinition
from phlo.plugins.discovery._service_dependency_resolution import resolve_service_dependencies
from phlo.plugins.discovery._service_loading import resolve_plugin_source_path
from phlo.plugins.discovery.registry import get_global_registry


def _is_service_yaml(filename: str) -> bool:
    return filename == "service.yaml" or filename.endswith(("-setup.yaml", "-daemon.yaml"))


def get_registered_service_plugins() -> dict[str, Any]:
    """Return every service plugin registered in the global registry, keyed by name."""
    registry = get_global_registry()
    return {
        name: plugin
        for name in registry.list("service")
        if (plugin := registry.get("service", name)) is not None
    }


def _companion_manifests(
    source_path: Path | None,
    existing_names: set[str],
) -> list[ServiceManifest]:
    if not source_path or not source_path.exists():
        return []

    manifests: list[ServiceManifest] = []
    for yaml_path in sorted(source_path.rglob("*.yaml")):
        if yaml_path.name == "service.yaml":
            continue
        if not yaml_path.name.endswith(("-setup.yaml", "-daemon.yaml")):
            continue
        definition = ServiceDefinition.from_yaml(yaml_path)
        if definition.name in existing_names:
            continue
        existing_names.add(definition.name)
        manifests.append(ServiceManifest(definition=definition, source_path=yaml_path))
    return manifests


class ServiceManifestError(ValueError):
    """Raised when a service package manifest cannot be resolved."""

    def __init__(
        self,
        message: str,
        *,
        service_name: str | None = None,
        source_path: Path | None = None,
    ) -> None:
        self.message = message
        self.service_name = service_name
        self.source_path = source_path
        super().__init__(self.__str__())

    def __str__(self) -> str:
        details: list[str] = []
        if self.service_name:
            details.append(f"service={self.service_name}")
        if self.source_path:
            details.append(f"source={self.source_path}")
        if not details:
            return self.message
        return f"{self.message}: {' '.join(details)}"


@dataclass(frozen=True, slots=True)
class ServiceManifest:
    """Resolved service package manifest with source context."""

    definition: ServiceDefinition
    source_path: Path | None = None

    @property
    def name(self) -> str:
        """Service name from the underlying definition."""
        return self.definition.name


@dataclass(slots=True)
class ServiceManifestResolver:
    """Resolve service package manifests from plugins and local service directories."""

    services_dir: Path | None = None

    def resolve_plugin_manifests(self) -> list[ServiceManifest]:
        """Discover registered service plugins and build manifests from their definitions,
        including sibling setup/daemon manifests beside each plugin source.
        """
        discover_plugins(plugin_type="service", auto_register=True)
        manifests: list[ServiceManifest] = []
        names: set[str] = set()
        for name, plugin in get_registered_service_plugins().items():
            source_path = resolve_plugin_source_path(plugin)
            try:
                definition = ServiceDefinition.from_dict(plugin.service_definition, source_path)
            except (KeyError, ValueError) as exc:
                raise ServiceManifestError(
                    "invalid plugin service definition",
                    service_name=name,
                    source_path=source_path,
                ) from exc
            if definition.name in names:
                continue
            names.add(definition.name)
            manifests.append(ServiceManifest(definition=definition, source_path=source_path))
            manifests.extend(_companion_manifests(source_path, names))
        return manifests

    def resolve_directory_manifests(self) -> list[ServiceManifest]:
        """Parse service YAML files under services_dir into manifests, skipping schema files."""
        if not self.services_dir or not self.services_dir.exists():
            return []

        manifests: list[ServiceManifest] = []
        for yaml_path in sorted(self.services_dir.rglob("*.yaml")):
            if ".schema" in str(yaml_path):
                continue
            if not _is_service_yaml(yaml_path.name):
                continue
            try:
                definition = ServiceDefinition.from_yaml(yaml_path)
            except (yaml.YAMLError, KeyError, ValueError) as exc:
                raise ServiceManifestError(
                    "invalid service definition file",
                    source_path=yaml_path,
                ) from exc
            manifests.append(ServiceManifest(definition=definition, source_path=yaml_path))
        return manifests

    @staticmethod
    def expand_dependencies(
        services: list[ServiceDefinition],
        requested_names: list[str],
    ) -> list[ServiceDefinition]:
        """Expand the requested services to their full dependency closure in dependency order."""
        service_by_name = {service.name: service for service in services}
        missing = [name for name in requested_names if name not in service_by_name]
        if missing:
            raise ServiceManifestError(
                "unknown service dependency request",
                service_name=", ".join(missing),
            )

        selected: dict[str, ServiceDefinition] = {}

        def include(service: ServiceDefinition) -> None:
            """Recursively include a service and everything it depends on."""
            for dependency_name in service.depends_on:
                dependency = service_by_name.get(dependency_name)
                if dependency is not None:
                    include(dependency)
            selected[service.name] = service

        for name in requested_names:
            include(service_by_name[name])
        # A "-setup" service is pulled in automatically, but only when every
        # dependency it declares is already part of the selection; a setup job
        # whose dependencies were not requested must not drag in new services.

        bootstrap_companions = [
            service
            for service in services
            if service.name.endswith("-setup")
            and service.depends_on
            and all(dependency in selected for dependency in service.depends_on)
        ]
        for companion in bootstrap_companions:
            selected.setdefault(companion.name, companion)

        return resolve_service_dependencies(list(selected.values()))
