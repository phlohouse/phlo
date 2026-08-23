"""Regression tests for phlo's public API exports.

Guards compatibility aliases, lazy discovery exports, observatory re-exports,
and that importing plugins never pulls provider-only dependencies.
"""

from __future__ import annotations

import importlib
import sys
import types
from builtins import __import__ as builtin_import
from typing import Any, cast

import pytest

pytestmark = pytest.mark.core_regression


def test_phlo_ingestion_module_is_dlt_compatibility_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """phlo.ingestion should remain callable while dispatching through phlo.ingest.dlt."""
    import phlo

    calls: list[dict[str, str]] = []

    def fake_dlt(**kwargs: str) -> str:
        calls.append(kwargs)
        return "decorator"

    monkeypatch.setattr(phlo.ingest, "dlt", fake_dlt)
    monkeypatch.setattr(phlo.ingest, "assets", lambda provider_name=None: ["dlt_asset"])

    assert callable(phlo.ingestion)
    assert phlo.ingestion(table_name="events") == "decorator"
    assert phlo.ingestion.phlo_ingestion(table_name="events") == "decorator"
    assert phlo.ingestion.get_ingestion_assets() == ["dlt_asset"]
    assert calls == [{"table_name": "events"}, {"table_name": "events"}]


def test_quality_module_import_does_not_load_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing phlo.quality should define no provider-backed exports eagerly."""
    import phlo.plugins.discovery as discovery

    def _fail_discovery() -> None:
        raise AssertionError("quality provider discovery should be lazy")

    monkeypatch.setattr(discovery, "discover_plugins", _fail_discovery)
    monkeypatch.delitem(sys.modules, "phlo.quality", raising=False)

    quality_module = importlib.import_module("phlo.quality")

    assert "phlo_quality" not in quality_module.__dict__


def test_quality_module_populates_exports_on_discovered_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovered quality providers must hydrate all public exports."""
    provider_package_name = "fake_quality_provider_pkg"
    provider_module_name = f"{provider_package_name}.plugin"

    class _DummyCheck:
        pass

    class _DummyContract:
        pass

    def _get_quality_checks() -> list[str]:
        return ["check"]

    def _clear_quality_checks() -> None:
        return None

    def _dbt_check_name(test_type: str, target: str) -> str:
        return f"{test_type}_{target}"

    provider_package = cast(Any, types.ModuleType(provider_package_name))
    provider_package.get_quality_checks = _get_quality_checks
    provider_package.clear_quality_checks = _clear_quality_checks
    provider_package.CustomSQLCheck = _DummyCheck
    provider_package.QualityCheckContract = _DummyContract
    provider_package.PANDERA_CONTRACT_CHECK_NAME = "pandera_contract"
    provider_package.dbt_check_name = _dbt_check_name

    provider_plugin_module = types.ModuleType(provider_module_name)

    monkeypatch.setitem(sys.modules, provider_package_name, provider_package)
    monkeypatch.setitem(sys.modules, provider_module_name, provider_plugin_module)
    monkeypatch.delitem(sys.modules, "phlo.quality", raising=False)

    class _Provider:
        __module__ = provider_module_name

        def get_decorator(self):
            return lambda fn: fn

        def get_check_classes(self) -> dict[str, type]:
            return {
                "null": _DummyCheck,
                "range": _DummyCheck,
                "freshness": _DummyCheck,
                "unique": _DummyCheck,
                "count": _DummyCheck,
                "schema": _DummyCheck,
                "pattern": _DummyCheck,
                "quality_check": _DummyCheck,
            }

        def get_reconciliation_checks(self) -> dict[str, type]:
            return {
                "reconciliation": _DummyCheck,
                "aggregate_consistency": _DummyCheck,
                "aggregate_spec": _DummyCheck,
                "key_parity": _DummyCheck,
                "multi_aggregate": _DummyCheck,
                "checksum": _DummyCheck,
            }

    import phlo.plugins.discovery as discovery

    class _Registry:
        def get(self, plugin_type: str, name: str):
            assert plugin_type == "quality_provider"
            assert name == "pandera"
            return _Provider()

    monkeypatch.setattr(discovery, "discover_plugins", lambda: None)
    monkeypatch.setattr(discovery, "get_global_registry", lambda: _Registry())

    quality_module = importlib.import_module("phlo.quality")

    assert callable(quality_module.get_quality_checks)
    assert callable(quality_module.clear_quality_checks)
    assert quality_module.get_quality_checks() == ["check"]
    assert quality_module.QualityCheck is _DummyCheck
    assert quality_module.CustomSQLCheck is _DummyCheck
    assert quality_module.QualityCheckContract is _DummyContract
    assert quality_module.PANDERA_CONTRACT_CHECK_NAME == "pandera_contract"
    assert callable(quality_module.dbt_check_name)
    assert quality_module.dbt_check_name("dbt", "users") == "dbt_users"


def test_plugin_discovery_exports_generic_query_helpers() -> None:
    """Plugin discovery should expose generic registry query helpers for public APIs."""
    import phlo.plugins.discovery as discovery

    assert callable(discovery.get_plugin)
    assert callable(discovery.list_plugins)
    assert "ingestion_provider" in discovery.PLUGIN_FAMILIES
    assert "quality_provider" in discovery.PLUGIN_FAMILIES


def test_plugin_query_helpers_list_registered_provider_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider listing helpers should delegate to the global plugin registry."""
    from phlo.plugins.discovery import _plugin_queries

    class _Registry:
        def list_all_plugins(self) -> dict[str, list[str]]:
            return {
                "ingestion_provider": ["dlt", "sling"],
                "quality_provider": ["pandera"],
            }

    monkeypatch.setattr(_plugin_queries, "get_global_registry", lambda: _Registry())

    assert _plugin_queries.list_plugins("ingestion_provider") == {
        "ingestion_provider": ["dlt", "sling"]
    }
    assert _plugin_queries.list_plugins("quality_provider") == {"quality_provider": ["pandera"]}


def test_plugins_module_reexports_generic_plugin_queries() -> None:
    """phlo.plugins should expose generic plugin query helpers."""
    import phlo.plugins as plugins
    import phlo.plugins.discovery as discovery

    assert "get_plugin" in plugins.__all__
    assert "list_plugins" in plugins.__all__
    assert "get_ingestion_provider" not in plugins.__all__
    assert plugins.get_plugin is discovery.get_plugin
    assert plugins.list_plugins is discovery.list_plugins


def test_plugins_module_lazy_discovery_exports_use_importlib(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lazy discovery exports should resolve through importlib without recursive lookups."""
    import phlo.plugins as plugins
    import phlo.plugins.discovery as discovery

    imports: list[str] = []
    original_import_module = plugins.importlib.import_module

    def _record_import(name: str, package: str | None = None) -> object:
        imports.append(name)
        return original_import_module(name, package)

    monkeypatch.setattr(plugins.importlib, "import_module", _record_import)

    assert plugins.__getattr__("discovery") is discovery
    assert plugins.get_plugin is discovery.get_plugin
    assert plugins.list_plugins is discovery.list_plugins
    plugin_imports = [name for name in imports if name.startswith("phlo.plugins")]
    assert plugin_imports.count("phlo.plugins.discovery") == 3
    assert "phlo.plugins" not in plugin_imports


def test_plugins_module_all_includes_lazy_discovery_exports() -> None:
    """Every supported lazy discovery helper should be advertised as a public export."""
    import phlo.plugins as plugins

    for export in plugins._LAZY_DISCOVERY_EXPORTS:
        assert export in plugins.__all__


def test_plugins_module_reexports_observatory_contracts() -> None:
    """phlo.plugins should expose Observatory extension contracts from core."""
    import phlo.plugins as plugins
    from phlo.plugins.observatory import ObservatoryExtensionManifest, ObservatoryExtensionPlugin

    assert "ObservatoryExtensionPlugin" in plugins.__all__
    assert "ObservatoryExtensionManifest" in plugins.__all__
    assert plugins.ObservatoryExtensionPlugin is ObservatoryExtensionPlugin
    assert plugins.ObservatoryExtensionManifest is ObservatoryExtensionManifest


def test_plugins_module_reexports_observatory_settings_contracts() -> None:
    """phlo.plugins should expose Observatory settings storage contracts from core."""
    import phlo.plugins as plugins
    from phlo.plugins.observatory_settings import SettingsScope, get_settings_service

    assert "SettingsScope" in plugins.__all__
    assert "get_settings_service" in plugins.__all__
    assert plugins.SettingsScope is SettingsScope
    assert plugins.get_settings_service is get_settings_service
    # Core must not re-export the concrete SettingsService — it lives in phlo-postgres.
    assert "SettingsService" not in plugins.__all__


def test_plugins_module_imports_without_psycopg2(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing phlo.plugins should not require optional psycopg2."""

    def _block_psycopg2(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "psycopg2":
            raise ModuleNotFoundError("No module named 'psycopg2'")
        return builtin_import(name, globals, locals, fromlist, level)

    monkeypatch.delitem(sys.modules, "phlo.plugins", raising=False)
    monkeypatch.delitem(sys.modules, "phlo.plugins.observatory_settings", raising=False)
    monkeypatch.setattr("builtins.__import__", _block_psycopg2)

    plugins = importlib.import_module("phlo.plugins")

    # Core contracts are importable without psycopg2.
    assert "SettingsScope" in plugins.__all__
    assert "get_settings_service" in plugins.__all__
    # The concrete SettingsService (which uses psycopg2) must not be in core.
    assert "SettingsService" not in plugins.__all__
