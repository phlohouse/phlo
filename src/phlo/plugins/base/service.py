"""Service plugin classes.

This module defines plugin types for Docker-based infrastructure components.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from typing import Any

from phlo.plugins.base.plugin import Plugin, PluginMetadata


class ServicePlugin(Plugin, ABC):
    """Base class for service plugins.

    Service plugins provide Docker-based infrastructure components
    that can be composed into a Phlo stack.
    """

    @property
    @abstractmethod
    def service_definition(self) -> dict[str, Any]:
        """Return the service definition.

        This is equivalent to the content of a service.yaml file.
        """

    @property
    def category(self) -> str:
        """Service category (core, api, bi, observability, etc.)."""
        return self.service_definition.get("category", "custom")

    @property
    def is_default(self) -> bool:
        """Whether this service should be installed by default."""
        return self.service_definition.get("default", False)

    @property
    def profile(self) -> str | None:
        """Optional profile this service belongs to."""
        return self.service_definition.get("profile")

    def get_compose_fragment(self) -> dict[str, Any]:
        """Return Docker Compose service configuration."""
        return self.service_definition.get("compose", {})

    def get_files(self) -> list[dict[str, str]]:
        """Return files to copy during initialization."""
        return self.service_definition.get("files", [])

    def get_dependencies(self) -> list[str]:
        """Return list of service names this depends on."""
        return self.service_definition.get("depends_on", [])

    @property
    def requires_capabilities(self) -> list[str]:
        """Return required capabilities for this service plugin."""
        return list(self.metadata.requires_capabilities)

    @property
    def optional_capabilities(self) -> list[str]:
        """Return optional capabilities for this service plugin."""
        return list(self.metadata.optional_capabilities)


class PackageYamlServicePlugin(ServicePlugin, ABC):
    """ServicePlugin that loads service_definition from a package YAML file.

    Subclasses inherit a default ``service_definition`` property that loads
    from ``service.yaml`` inside the subclass's top-level package.  Override
    ``_service_definition_file`` or ``_service_definition_package`` for
    non-default layouts.
    """

    _service_definition_file: str = "service.yaml"
    _service_definition_package: str | None = None

    @property
    def service_definition(self) -> dict[str, Any]:
        """Load service definition from package YAML resource."""
        from importlib import resources

        import yaml

        package = self._service_definition_package or self.__class__.__module__.split(".", 1)[0]
        path = resources.files(package).joinpath(self._service_definition_file)
        return yaml.safe_load(path.read_text(encoding="utf-8"))


def service_plugin_class(
    class_name: str,
    *,
    name: str,
    version: str,
    description: str,
    author: str = "",
    tags: list[str] | None = None,
    service_definition_file: str = "service.yaml",
    service_definition_package: str | None = None,
) -> type[PackageYamlServicePlugin]:
    """Create a YAML-backed service plugin class from static metadata."""
    frame = inspect.currentframe()
    caller = frame.f_back if frame is not None else None
    caller_module = caller.f_globals.get("__name__", __name__) if caller is not None else __name__
    metadata = PluginMetadata(
        name=name,
        version=version,
        description=description,
        author=author,
        tags=tags or [],
    )

    class DeclarativeYamlServicePlugin(PackageYamlServicePlugin):
        _service_definition_file = service_definition_file
        _service_definition_package = service_definition_package or caller_module.split(".", 1)[0]

        @property
        def metadata(self) -> PluginMetadata:
            """Return the static metadata captured when the class was declared."""
            return metadata

    DeclarativeYamlServicePlugin.__name__ = class_name
    DeclarativeYamlServicePlugin.__qualname__ = class_name
    DeclarativeYamlServicePlugin.__module__ = caller_module
    return DeclarativeYamlServicePlugin
