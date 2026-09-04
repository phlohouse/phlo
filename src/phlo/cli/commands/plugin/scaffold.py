"""Plugin package scaffolding — template strings and file-writing logic.

Each plugin type maps to a base class, an entry-point group, and a
method-stub template; create_plugin_package renders those into a new
package (init, plugin module, test, pyproject, README). Purely
generative: it writes files and never registers the plugin itself.
"""

from __future__ import annotations

from pathlib import Path

_TYPE_TO_BASE_CLASS = {
    "source": "SourceConnectorPlugin",
    "quality": "QualityCheckPlugin",
    "transform": "TransformationPlugin",
    "service": "ServicePlugin",
    "hook": "HookPlugin",
    "catalog": "CatalogPlugin",
    "asset": "AssetProviderPlugin",
    "resource": "ResourceProviderPlugin",
    "orchestrator": "OrchestratorAdapterPlugin",
}

_TYPE_TO_ENTRY_POINT = {
    "source": "phlo.plugins.sources",
    "quality": "phlo.plugins.quality",
    "transform": "phlo.plugins.transforms",
    "service": "phlo.plugins.services",
    "hook": "phlo.plugins.hooks",
    "catalog": "phlo.plugins.catalogs",
    "asset": "phlo.plugins.assets",
    "resource": "phlo.plugins.resources",
    "orchestrator": "phlo.plugins.orchestrators",
}

_PLUGIN_TYPE_TEMPLATES = {
    "source": '''
    def fetch_data(self, config: dict):
        """Fetch data from source."""
        # Implement your data fetching logic here
        raise NotImplementedError()

    def get_schema(self, config: dict) -> dict | None:
        """Get source schema."""
        # Return schema or None
        return None
''',
    "quality": '''
    def create_check(self, **kwargs):
        """Create quality check instance."""
        # Implement your quality check creation logic here
        raise NotImplementedError()
''',
    "transform": '''
    def transform(self, df, config: dict):
        """Transform dataframe."""
        # Implement your transformation logic here
        raise NotImplementedError()

    def get_output_schema(self, input_schema: dict, config: dict) -> dict | None:
        """Get output schema."""
        # Return schema or None
        return None

    def validate_config(self, config: dict) -> bool:
        """Validate transformation configuration."""
        # Add config validation logic here
        return True
''',
    "service": '''
    @property
    def service_definition(self) -> dict:
        """Return service definition."""
        return {
            "category": "custom",
            "compose": {
                "image": "your-service:latest",
            },
        }
''',
    "catalog": '''
    @property
    def targets(self) -> list[str]:
        """Return engine targets for this catalog."""
        return []

    @property
    def catalog_name(self) -> str:
        """Return catalog name."""
        return "example"

    def get_properties(self) -> dict[str, str]:
        """Return catalog properties."""
        return {"connector.name": "example"}
''',
    "asset": '''
    def get_assets(self) -> Iterable[AssetSpec]:
        """Return asset specs."""
        # Add asset definitions here
        return []

    def get_checks(self) -> Iterable[AssetCheckSpec]:
        """Return asset check specs."""
        # Add asset checks here
        return []
''',
    "resource": '''
    def get_resources(self) -> Iterable[ResourceSpec]:
        """Return resource specs."""
        # Add resource definitions here
        return []
''',
    "orchestrator": '''
    def build_definitions(
        self,
        *,
        assets: Iterable[AssetSpec],
        checks: Iterable[AssetCheckSpec],
        resources: Iterable[ResourceSpec],
    ):
        """Build orchestrator definitions from capability specs."""
        # Implement orchestrator-specific translation here
        raise NotImplementedError()
''',
}

_README_INTERNAL_TYPE_MAP = {
    "hook": "hook",
    "source": "source_connector",
    "quality": "quality_check",
    "transform": "transformation",
    "service": "service",
    "catalog": "catalog",
    "asset": "asset_provider",
    "resource": "resource_provider",
    "orchestrator": "orchestrator",
}


def _build_init_content(plugin_name: str, plugin_type: str, module_name: str) -> str:
    class_name = plugin_name.replace("-", "_").title().replace("_", "") + "Plugin"
    return f'''"""
{plugin_name} plugin for Phlo

Plugin type: {plugin_type}
"""

from importlib.metadata import version

from phlo_{module_name}.plugin import {class_name}

__all__ = ["{class_name}"]
__version__ = version("{plugin_name}")
'''


def _build_plugin_content(
    plugin_name: str, plugin_type: str, base_class: str, class_name: str
) -> str:
    content = f'''"""
{plugin_name} plugin implementation.
"""

from phlo.plugins import {base_class}, PluginMetadata
'''

    if plugin_type in {"asset", "resource", "orchestrator"}:
        content += "\nfrom collections.abc import Iterable\n"
        spec_imports: list[str]
        if plugin_type == "resource":
            spec_imports = ["ResourceSpec"]
        elif plugin_type == "asset":
            spec_imports = ["AssetCheckSpec", "AssetSpec"]
        else:
            spec_imports = ["AssetCheckSpec", "AssetSpec", "ResourceSpec"]
        content += f"from phlo.capabilities.specs import {', '.join(spec_imports)}\n"

    if plugin_type == "hook":
        content += f'''
from phlo.hooks import HookEvent
from phlo.plugins import HookFilter, HookRegistration


class {class_name}({base_class}):
    """
    {plugin_name} hook plugin.

    Add your hook handlers here.
    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata."""
        return PluginMetadata(
            name="{plugin_name}",
            version="0.1.0",
            description="Add description here",
            author="Your Name",
        )

    def get_hooks(self):
        """Return hook registrations."""
        return [
            HookRegistration(
                hook_name="handle_events",
                handler=self.handle_event,
                filters=HookFilter(event_types={{"quality.result"}}),
            )
        ]

    def handle_event(self, event: HookEvent) -> None:
        """Handle hook events."""
        # Add your hook logic here
        raise NotImplementedError()
'''
    else:
        content += f'''


class {class_name}({base_class}):
    """
    {plugin_name} plugin.

    Add your implementation here.
    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata."""
        return PluginMetadata(
            name="{plugin_name}",
            version="0.1.0",
            description="Add description here",
            author="Your Name",
        )

    def initialize(self, config: dict) -> None:
        """Initialize plugin with configuration."""
        super().initialize(config)
        # Add initialization logic here

    def cleanup(self) -> None:
        """Clean up plugin resources."""
        super().cleanup()
        # Add cleanup logic here
'''

    content += _PLUGIN_TYPE_TEMPLATES.get(plugin_type, "")
    return content


def _build_test_content(plugin_name: str, module_name: str, class_name: str) -> str:
    return f'''"""
Tests for {plugin_name} plugin.
"""

import pytest
from phlo_{module_name}.plugin import {class_name}


@pytest.fixture
def plugin():
    """Create plugin instance."""
    return {class_name}()


def test_plugin_metadata(plugin):
    """Test plugin metadata."""
    metadata = plugin.metadata
    assert metadata.name == "{plugin_name}"
    assert metadata.version == "0.1.0"
    assert metadata.author is not None


def test_plugin_initialization(plugin):
    """Test plugin initialization."""
    if hasattr(plugin, "initialize"):
        config = {{}}
        plugin.initialize(config)
        # Add more initialization tests


def test_plugin_cleanup(plugin):
    """Test plugin cleanup."""
    if hasattr(plugin, "cleanup"):
        plugin.cleanup()
        # Add more cleanup tests
'''


def _build_pyproject_content(
    plugin_name: str,
    plugin_type: str,
    module_name: str,
    class_name: str,
    entry_point_group: str,
) -> str:
    return f'''[build-system]
requires = ["setuptools>=45", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{plugin_name}"
version = "0.1.0"
description = "Phlo {plugin_type} plugin"
readme = "README.md"
requires-python = ">=3.11"
authors = [
    {{name = "Your Name", email = "your@email.com"}},
]
license = {{text = "MIT"}}
dependencies = [
    "phlo>=0.1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-cov>=4.0",
    "ruff>=0.1.0",
    "basedpyright>=1.0.0",
]

[project.entry-points."{entry_point_group}"]
{plugin_name} = "phlo_{module_name}.plugin:{class_name}"

[tool.setuptools]
package-dir = {{"" = "src"}}

[tool.setuptools.packages.find]
where = ["src"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.basedpyright]
typeCheckingMode = "standard"
'''


def _build_readme_content(
    plugin_name: str, plugin_type: str, module_name: str, class_name: str
) -> str:
    internal_type = _README_INTERNAL_TYPE_MAP.get(plugin_type, plugin_type)

    content = f"""# {plugin_name}

A Phlo {plugin_type} plugin.

## Installation

```bash
pip install -e .
```

## Usage

```python
from phlo.plugins import get_plugin
from phlo_{module_name} import {class_name}

plugin = {class_name}()

registered_plugin = get_plugin("{internal_type}", "{plugin_name}")
```
"""
    content += """

## Development

Install development dependencies:
```bash
pip install -e ".[dev]"
```

Run tests:
```bash
pytest tests/
```

Run linting:
```bash
ruff check .
basedpyright .
```

## License

MIT
"""
    return content


def create_plugin_package(plugin_name: str, plugin_type: str, plugin_path: Path):
    """Create plugin package structure and files."""
    src_dir = plugin_path / "src" / f"phlo_{plugin_name.replace('-', '_')}"
    src_dir.mkdir(parents=True, exist_ok=True)
    tests_dir = plugin_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    module_name = plugin_name.replace("-", "_")
    base_class = _TYPE_TO_BASE_CLASS.get(plugin_type)
    if base_class is None:
        raise ValueError(f"unknown plugin_type: {plugin_type}")

    entry_point_group = _TYPE_TO_ENTRY_POINT[plugin_type]
    class_name = plugin_name.replace("-", "_").title().replace("_", "") + "Plugin"

    (src_dir / "__init__.py").write_text(_build_init_content(plugin_name, plugin_type, module_name))
    (src_dir / "plugin.py").write_text(
        _build_plugin_content(plugin_name, plugin_type, base_class, class_name)
    )
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / "test_plugin.py").write_text(
        _build_test_content(plugin_name, module_name, class_name)
    )
    (plugin_path / "pyproject.toml").write_text(
        _build_pyproject_content(
            plugin_name, plugin_type, module_name, class_name, entry_point_group
        )
    )
    (plugin_path / "README.md").write_text(
        _build_readme_content(plugin_name, plugin_type, module_name, class_name)
    )
    (plugin_path / "MANIFEST.in").write_text("include README.md\nrecursive-include src *.py\n")
