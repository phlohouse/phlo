"""Base classes for Phlo plugins.

This module defines the abstract base classes that all plugin types must inherit
from and implement. These interfaces provide a standardized contract for extending
Phlo's functionality through plugins.

Plugin Types:
    - :class:`Plugin`: Base class for all plugins
    - :class:`SourceConnectorPlugin`: Data source connectors
    - :class:`QualityCheckPlugin`: Data quality validation
    - :class:`QualityProviderPlugin`: Quality check providers
    - :class:`IngestionProviderPlugin`: Data ingestion providers
    - :class:`TransformationProviderPlugin`: Transformation providers
    - :class:`ServicePlugin`: Infrastructure services
    - :class:`CatalogPlugin`: Metadata catalogs
    - :class:`GovernancePlugin`: Data governance features
    - :class:`AssetProviderPlugin`: Asset definitions
    - :class:`ResourceProviderPlugin`: Resource definitions
    - :class:`OrchestratorAdapterPlugin`: Orchestrator integrations
    - :class:`CliCommandPlugin`: CLI command extensions

Each plugin type defines:
    - :attr:`PluginMetadata`: Plugin metadata (name, version, description)
    - :meth:`Plugin.initialize`: Lifecycle initialization
    - :meth:`Plugin.cleanup`: Lifecycle cleanup
    - Type-specific abstract methods

Example:
    ```python
    from phlo.plugins.base import Plugin, PluginMetadata
    from phlo.capabilities.support import CapabilitySupport

    class MyPlugin(Plugin):
        @property
        def metadata(self) -> PluginMetadata:
            return PluginMetadata(
                name="my-plugin",
                version="1.0.0",
                description="A custom Phlo plugin",
                author="My Name",
                support=CapabilitySupport()
            )

        def initialize(self, config: dict) -> None:
            # Setup connections, load resources
            pass
    ```

See Also:
    - :mod:`phlo.plugins.discovery`: Plugin discovery and loading
    - :mod:`phlo.capabilities`: Capability system for plugins

"""

from __future__ import annotations

from phlo.plugins.base.catalog import CatalogPlugin
from phlo.plugins.base.cli import CliCommandPlugin, cli_command_plugin_class
from phlo.plugins.base.governance import GovernancePlugin
from phlo.plugins.base.ingestion_provider import IngestionProviderPlugin
from phlo.plugins.base.orchestrator import OrchestratorAdapterPlugin
from phlo.plugins.base.plugin import Plugin, PluginMetadata
from phlo.plugins.base.providers import AssetProviderPlugin, ResourceProviderPlugin
from phlo.plugins.base.quality import QualityCheckPlugin
from phlo.plugins.base.quality_provider import QualityProviderPlugin
from phlo.plugins.base.service import PackageYamlServicePlugin, ServicePlugin, service_plugin_class
from phlo.plugins.base.source import SourceConnectorPlugin
from phlo.plugins.base.transformation_provider import TransformationProviderPlugin

__all__ = [
    "Plugin",
    "PluginMetadata",
    "CliCommandPlugin",
    "cli_command_plugin_class",
    "SourceConnectorPlugin",
    "QualityCheckPlugin",
    "QualityProviderPlugin",
    "IngestionProviderPlugin",
    "TransformationProviderPlugin",
    "ServicePlugin",
    "PackageYamlServicePlugin",
    "service_plugin_class",
    "CatalogPlugin",
    "GovernancePlugin",
    "AssetProviderPlugin",
    "ResourceProviderPlugin",
    "OrchestratorAdapterPlugin",
]
