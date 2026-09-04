"""
Phlo Plugin System

Enable community contributions through a plugin architecture.

Phlo provides a plugin system that allows developers to extend
the framework with custom:
- Source connectors (ingest data from new APIs/databases)
- Quality checks (custom validation logic)
- Transformations (custom data processing steps)
- Services (Docker-based infrastructure components)

## Plugin Types

### 1. Source Connector Plugins
Extend Phlo with new data sources (APIs, databases, file formats).

```python
from phlo.plugins import SourceConnectorPlugin

class MyAPIConnector(SourceConnectorPlugin):
    name = "my_api"
    version = "1.0.0"

    def fetch_data(self, config: dict) -> Iterator[dict]:
        # Implement data fetching logic
        pass
```

### 2. Quality Check Plugins
Add custom quality check types beyond the built-in checks.

```python
from phlo.plugins import QualityCheckPlugin

class CustomQualityCheck(QualityCheckPlugin):
    name = "custom_check"
    version = "1.0.0"

    def validate(self, df: pd.DataFrame) -> QualityCheckResult:
        # Implement custom validation logic
        pass
```

### 3. Transformation Plugins
Add custom transformation functions.

```python
from phlo.plugins import TransformationPlugin

class CustomTransform(TransformationPlugin):
    name = "custom_transform"
    version = "1.0.0"

    def transform(self, df: pd.DataFrame, config: dict) -> pd.DataFrame:
        # Implement transformation logic
        pass
```

### 4. Service Plugins
Add Docker-based infrastructure components.

```python
from phlo.plugins import ServicePlugin

class CustomService(ServicePlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="custom_service",
            version="1.0.0",
            description="Custom service",
        )

    @property
    def service_definition(self) -> dict:
        return {
            "category": "custom",
            "compose": {
                "image": "my-service:latest",
                "ports": ["1234:1234"],
            },
        }
```

## Installing Plugins

Plugins are installed as Python packages with entry points:

```toml
# Plugin package's pyproject.toml
[project.entry-points."phlo.plugins.sources"]
my_api = "my_phlo_plugin:MyAPIConnector"

[project.entry-points."phlo.plugins.quality"]
custom_check = "my_phlo_plugin:CustomQualityCheck"

[project.entry-points."phlo.plugins.transforms"]
custom_transform = "my_phlo_plugin:CustomTransform"

[project.entry-points."phlo.plugins.services"]
custom_service = "my_phlo_plugin:CustomService"
```

After installing the plugin package:
```bash
pip install my-phlo-plugin
```

The plugin is automatically discovered and available:
```python
from phlo.plugins import discover_plugins

# Discover all installed plugins
plugins = discover_plugins()

# Use plugin
from phlo.plugins import get_plugin
connector = get_plugin("source_connector", "my_api")
data = connector.fetch_data(config={...})
```

## Plugin Development Guide

See docs/PLUGIN_DEVELOPMENT.md for complete guide on developing plugins.

## Security

Plugins are loaded from installed Python packages only. Ensure you:
- Only install trusted plugins
- Review plugin source code before installation
- Use virtual environments to isolate plugins
"""

import importlib
from importlib.metadata import version
from typing import TYPE_CHECKING

from phlo.plugins.base import (
    AssetProviderPlugin,
    CatalogPlugin,
    OrchestratorAdapterPlugin,
    PackageYamlServicePlugin,
    Plugin,
    PluginMetadata,
    QualityCheckPlugin,
    QualityProviderPlugin,
    ResourceProviderPlugin,
    ServicePlugin,
    SourceConnectorPlugin,
    TransformationPlugin,
    cli_command_plugin_class,
    service_plugin_class,
)
from phlo.plugins.hooks import FailurePolicy, HookFilter, HookHandler, HookPlugin, HookProvider

if TYPE_CHECKING:
    from phlo.plugins.discovery import (
        PluginRegistry,
        discover_plugins,
        get_plugin,
        get_plugin_info,
        list_plugins,
        validate_plugins,
    )
    from phlo.plugins.observatory import (
        ObservatoryExtensionCompatibility,
        ObservatoryExtensionManifest,
        ObservatoryExtensionNavItem,
        ObservatoryExtensionPlugin,
        ObservatoryExtensionRoute,
        ObservatoryExtensionSettings,
        ObservatoryExtensionSettingsPanel,
        ObservatoryExtensionSlot,
        ObservatoryExtensionUI,
        discover_observatory_extensions,
        get_observatory_extension,
    )
    from phlo.plugins.observatory_settings import (
        SettingsRecord,
        SettingsScope,
        get_settings_service,
    )
    from phlo.plugins.semantic import SemanticLayerProvider, SemanticModel


# Import discovery functions lazily to avoid circular imports.
_LAZY_DISCOVERY_EXPORTS = frozenset(
    {
        "discover_plugins",
        "get_plugin",
        "get_plugin_info",
        "list_plugins",
        "validate_plugins",
        "PluginRegistry",
    }
)

_LAZY_MODULE_EXPORTS = {
    "ObservatoryExtensionPlugin": "phlo.plugins.observatory",
    "ObservatoryExtensionManifest": "phlo.plugins.observatory",
    "ObservatoryExtensionCompatibility": "phlo.plugins.observatory",
    "ObservatoryExtensionSettings": "phlo.plugins.observatory",
    "ObservatoryExtensionRoute": "phlo.plugins.observatory",
    "ObservatoryExtensionNavItem": "phlo.plugins.observatory",
    "ObservatoryExtensionSlot": "phlo.plugins.observatory",
    "ObservatoryExtensionSettingsPanel": "phlo.plugins.observatory",
    "ObservatoryExtensionUI": "phlo.plugins.observatory",
    "discover_observatory_extensions": "phlo.plugins.observatory",
    "get_observatory_extension": "phlo.plugins.observatory",
    "SettingsScope": "phlo.plugins.observatory_settings",
    "SettingsRecord": "phlo.plugins.observatory_settings",
    "get_settings_service": "phlo.plugins.observatory_settings",
    "SemanticLayerProvider": "phlo.plugins.semantic",
    "SemanticModel": "phlo.plugins.semantic",
}


def __getattr__(name):
    """Lazily expose discovery symbols to avoid import cycles; raises
    AttributeError for names that are not supported lazy exports.
    """
    if name == "discovery":
        return importlib.import_module("phlo.plugins.discovery")
    if name in _LAZY_DISCOVERY_EXPORTS:
        discovery_module = importlib.import_module("phlo.plugins.discovery")
        return getattr(discovery_module, name)
    if name in _LAZY_MODULE_EXPORTS:
        module = importlib.import_module(_LAZY_MODULE_EXPORTS[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Base classes
    "Plugin",
    "PluginMetadata",
    "SourceConnectorPlugin",
    "QualityCheckPlugin",
    "QualityProviderPlugin",
    "ServicePlugin",
    "PackageYamlServicePlugin",
    "service_plugin_class",
    "TransformationPlugin",
    "cli_command_plugin_class",
    "AssetProviderPlugin",
    "CatalogPlugin",
    "ResourceProviderPlugin",
    "OrchestratorAdapterPlugin",
    "HookPlugin",
    "HookProvider",
    "HookHandler",
    "HookFilter",
    "FailurePolicy",
    "ObservatoryExtensionPlugin",
    "ObservatoryExtensionManifest",
    "ObservatoryExtensionCompatibility",
    "ObservatoryExtensionSettings",
    "ObservatoryExtensionRoute",
    "ObservatoryExtensionNavItem",
    "ObservatoryExtensionSlot",
    "ObservatoryExtensionSettingsPanel",
    "ObservatoryExtensionUI",
    "discover_observatory_extensions",
    "get_observatory_extension",
    "SettingsScope",
    "SettingsRecord",
    "get_settings_service",
    "SemanticLayerProvider",
    "SemanticModel",
    # Discovery
    "discover_plugins",
    "list_plugins",
    "get_plugin",
    "get_plugin_info",
    "validate_plugins",
    # Registry
    "PluginRegistry",
]

__version__ = version("phlo")
