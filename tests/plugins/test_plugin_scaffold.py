"""Tests for plugin package scaffolding templates.

Verifies generated plugin projects carry the right capability-spec imports per
type, README lookup guidance for internal types, rejection of unknown plugin
types, and the service plugin project layout.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from phlo.cli.commands.plugin.scaffold import (
    _build_plugin_content,
    _build_readme_content,
    create_plugin_package,
)

pytestmark = pytest.mark.core_regression


def test_create_plugin_package_writes_service_plugin_project(tmp_path: Path) -> None:
    """Generated service plugin packages contain importable Python and entry points."""
    plugin_path = tmp_path / "phlo-weather-service"

    create_plugin_package("weather-service", "service", plugin_path)

    module_dir = plugin_path / "src" / "phlo_weather_service"
    init_content = (module_dir / "__init__.py").read_text(encoding="utf-8")
    plugin_content = (module_dir / "plugin.py").read_text(encoding="utf-8")
    pyproject_content = (plugin_path / "pyproject.toml").read_text(encoding="utf-8")
    readme_content = (plugin_path / "README.md").read_text(encoding="utf-8")
    test_content = (plugin_path / "tests" / "test_plugin.py").read_text(encoding="utf-8")

    ast.parse(init_content)
    ast.parse(plugin_content)
    ast.parse(test_content)
    assert "class WeatherServicePlugin(ServicePlugin):" in plugin_content
    assert '"phlo.plugins.services"' in pyproject_content
    assert 'weather-service = "phlo_weather_service.plugin:WeatherServicePlugin"' in (
        pyproject_content
    )
    assert "from phlo.plugins import get_plugin" in readme_content
    assert 'registered_plugin = get_plugin("service", "weather-service")' in readme_content
    assert 'pip install -e ".[dev]"' in readme_content
    assert (plugin_path / "MANIFEST.in").read_text(encoding="utf-8") == (
        "include README.md\nrecursive-include src *.py\n"
    )


@pytest.mark.parametrize(
    ("plugin_type", "expected_import", "expected_method"),
    [
        ("asset", "from phlo.capabilities.specs import AssetCheckSpec, AssetSpec", "get_assets"),
        ("resource", "from phlo.capabilities.specs import ResourceSpec", "get_resources"),
        (
            "orchestrator",
            "from phlo.capabilities.specs import AssetCheckSpec, AssetSpec, ResourceSpec",
            "build_definitions",
        ),
    ],
)
def test_build_plugin_content_includes_capability_spec_imports(
    plugin_type: str,
    expected_import: str,
    expected_method: str,
) -> None:
    """Capability plugin templates include the concrete spec imports they annotate."""
    content = _build_plugin_content(
        "example-plugin",
        plugin_type,
        {
            "asset": "AssetProviderPlugin",
            "resource": "ResourceProviderPlugin",
            "orchestrator": "OrchestratorAdapterPlugin",
        }[plugin_type],
        "ExamplePlugin",
    )

    ast.parse(content)
    assert "from collections.abc import Iterable" in content
    assert expected_import in content
    assert f"def {expected_method}" in content


def test_readme_uses_generic_plugin_lookup_for_internal_plugin_types() -> None:
    """README examples map internal plugin types to registry keys."""
    content = _build_readme_content(
        "lakehouse-catalog",
        "catalog",
        "lakehouse_catalog",
        "LakehouseCatalogPlugin",
    )

    assert "from phlo.plugins import get_plugin" in content
    assert 'registered_plugin = get_plugin("catalog", "lakehouse-catalog")' in content


def test_create_plugin_package_rejects_unknown_plugin_type(tmp_path: Path) -> None:
    """Unknown plugin types are rejected instead of producing template files."""
    with pytest.raises(ValueError, match="unknown plugin_type: unknown"):
        create_plugin_package("bad-plugin", "unknown", tmp_path / "bad-plugin")
