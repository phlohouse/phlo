"""Service loading helpers for plugin and file-backed service definitions.

Definitions merge into one dict keyed by service name: already-present names
win over plugin-provided ones, and invalid definitions are logged as warnings
and skipped instead of aborting discovery.
Imported by sibling phlo.plugins.discovery modules (services, service_manifest) and the plugin
check CLI command.
"""

from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
from typing import Any, cast

import yaml

from phlo.logging import get_logger, log_event
from phlo.plugins.base.service import ServicePlugin
from phlo.plugins.discovery._plugin_loading import discover_plugins as _discover_plugins
from phlo.plugins.discovery._service_definition import ServiceDefinition
from phlo.plugins.discovery.registry import get_global_registry

logger = get_logger(__name__)


def discover_plugins(plugin_type: str = "service", auto_register: bool = True):
    """Compatibility wrapper used by service discovery call sites and tests."""
    return _discover_plugins(plugin_type=plugin_type, auto_register=auto_register)


def is_service_yaml(filename: str) -> bool:
    """Return True for recognized service definition YAML file names."""
    return filename == "service.yaml" or filename.endswith(("-setup.yaml", "-daemon.yaml"))


def load_plugin_services(services: dict[str, ServiceDefinition]) -> int:
    """Load service definitions from installed service plugins."""
    loaded_count = 0
    discover_plugins(plugin_type="service", auto_register=True)
    registry = get_global_registry()

    for name in registry.list("service"):
        plugin = cast(ServicePlugin | None, registry.get("service", name))
        if not plugin:
            continue
        if name in services:
            logger.debug("plugin_service_skipped_core_exists", service_name=name)
            continue

        service_definition = plugin.service_definition
        source_path = resolve_plugin_source_path(plugin)
        try:
            service = ServiceDefinition.from_dict(service_definition, source_path)
            services[service.name] = service
            loaded_count += 1
            loaded_count += load_companion_service_files(source_path, services)
        except (KeyError, ValueError) as exc:
            log_event(
                logger,
                "warning",
                "service_plugin_definition_invalid",
                plugin_name=name,
                source_path=str(source_path) if source_path else None,
                error=str(exc),
            )
    return loaded_count


def load_services_from_directory(
    services_dir: Path | None, services: dict[str, ServiceDefinition]
) -> int:
    """Load service definitions from a configured local services directory."""
    if not services_dir or not services_dir.exists():
        return 0

    loaded_count = 0
    for yaml_path in services_dir.rglob("*.yaml"):
        if ".schema" in str(yaml_path):
            continue
        if not is_service_yaml(yaml_path.name):
            continue

        try:
            service = ServiceDefinition.from_yaml(yaml_path)
            if service.name in services:
                continue
            services[service.name] = service
            loaded_count += 1
        except (yaml.YAMLError, KeyError, ValueError) as exc:
            log_event(
                logger,
                "warning",
                "service_definition_file_load_failed",
                path=str(yaml_path),
                error=str(exc),
            )
    return loaded_count


def load_companion_service_files(
    source_path: Path | None, services: dict[str, ServiceDefinition]
) -> int:
    """Load companion service YAMLs (for example *-setup.yaml) from a package path."""
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
            if service.name in services:
                continue
            services[service.name] = service
            loaded_count += 1
        except (yaml.YAMLError, KeyError, ValueError) as exc:
            log_event(
                logger,
                "warning",
                "companion_service_definition_file_load_failed",
                path=str(yaml_path),
                source_path=str(source_path),
                error=str(exc),
            )
    return loaded_count


def resolve_plugin_source_path(plugin: Any) -> Path | None:
    """Resolve plugin package path for companion service file discovery."""
    module_name = plugin.__class__.__module__
    package_name = module_name.split(".", 1)[0]
    spec = find_spec(package_name)
    if not spec or not spec.origin:
        return None
    return Path(spec.origin).parent
