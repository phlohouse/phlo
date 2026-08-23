"""Tests for provider-neutral ingestion public API.

Covers phlo.ingest: named-provider decorator resolution, install-focused
errors for missing providers, dlt/sling convenience aliases, asset
retrieval per provider or across all, and single-run discovery shared by
all providers.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any

import pytest

pytestmark = pytest.mark.core_regression


class _FakeProvider:
    def __init__(self, name: str) -> None:
        self.name = name

    def get_decorator(self):
        def _decorator(**kwargs: Any):
            def _wrap(fn):
                fn._provider_name = self.name
                fn._provider_kwargs = kwargs
                return fn

            return _wrap

        return _decorator

    def get_asset_retriever(self):
        return lambda: [f"{self.name}_asset"]


class _FakeRegistry:
    def __init__(self, providers: dict[str, _FakeProvider]) -> None:
        self.providers = providers

    def get(self, plugin_type: str, name: str):
        assert plugin_type == "ingestion_provider"
        return self.providers.get(name)

    def list(self, plugin_type: str) -> list[str]:
        assert plugin_type == "ingestion_provider"
        return list(self.providers)


def test_ingest_provider_returns_named_provider_decorator(monkeypatch: pytest.MonkeyPatch) -> None:
    """phlo.ingest.provider should resolve decorators from installed ingestion providers."""
    import phlo.plugins.discovery as discovery

    monkeypatch.setattr(discovery, "discover_plugins", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        discovery,
        "get_global_registry",
        lambda: _FakeRegistry({"sling": _FakeProvider("sling")}),
    )
    monkeypatch.delitem(sys.modules, "phlo.ingest", raising=False)

    ingest = importlib.import_module("phlo.ingest")

    @ingest.provider("sling")(table_name="users")
    def users() -> None:
        return None

    assert users._provider_name == "sling"
    assert users._provider_kwargs == {"table_name": "users"}


def test_ingest_provider_raises_clear_error_for_missing_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing ingestion providers should produce install-focused guidance."""
    import phlo.plugins.discovery as discovery

    monkeypatch.setattr(discovery, "discover_plugins", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        discovery,
        "get_global_registry",
        lambda: _FakeRegistry({"dlt": _FakeProvider("dlt")}),
    )
    monkeypatch.delitem(sys.modules, "phlo.ingest", raising=False)

    ingest = importlib.import_module("phlo.ingest")

    with pytest.raises(ModuleNotFoundError) as exc_info:
        ingest.provider("sling")

    assert "Ingestion provider 'sling' is not installed" in str(exc_info.value)
    assert "Installed ingestion providers: dlt" in str(exc_info.value)


def test_ingest_dlt_and_sling_aliases_resolve_named_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Convenience aliases should dispatch to explicit providers."""
    import phlo.plugins.discovery as discovery

    seen: list[str] = []

    class _SeenRegistry(_FakeRegistry):
        def get(self, plugin_type: str, name: str):
            seen.append(name)
            return super().get(plugin_type, name)

    monkeypatch.setattr(discovery, "discover_plugins", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        discovery,
        "get_global_registry",
        lambda: _SeenRegistry({"dlt": _FakeProvider("dlt"), "sling": _FakeProvider("sling")}),
    )
    monkeypatch.delitem(sys.modules, "phlo.ingest", raising=False)

    ingest = importlib.import_module("phlo.ingest")

    assert callable(ingest.dlt(table_name="events"))
    assert callable(ingest.sling(stream_name="public.users"))
    assert seen == ["dlt", "sling"]


def test_ingest_assets_can_return_all_or_one_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asset retrieval should support all providers and individual providers."""
    import phlo.plugins.discovery as discovery

    providers = {"dlt": _FakeProvider("dlt"), "sling": _FakeProvider("sling")}

    monkeypatch.setattr(discovery, "discover_plugins", lambda *args, **kwargs: None)
    monkeypatch.setattr(discovery, "get_global_registry", lambda: _FakeRegistry(providers))
    monkeypatch.delitem(sys.modules, "phlo.ingest", raising=False)

    ingest = importlib.import_module("phlo.ingest")

    assert ingest.assets("dlt") == ["dlt_asset"]
    assert ingest.assets() == ["dlt_asset", "sling_asset"]


def test_ingest_assets_discovers_once_for_all_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collecting all provider assets should not rediscover for each provider."""
    import phlo.plugins.discovery as discovery

    discovery_calls = 0
    providers = {"dlt": _FakeProvider("dlt"), "sling": _FakeProvider("sling")}

    def _discover_plugins(*args: Any, **kwargs: Any) -> None:
        nonlocal discovery_calls
        discovery_calls += 1

    monkeypatch.setattr(discovery, "discover_plugins", _discover_plugins)
    monkeypatch.setattr(discovery, "get_global_registry", lambda: _FakeRegistry(providers))
    monkeypatch.delitem(sys.modules, "phlo.ingest", raising=False)

    ingest = importlib.import_module("phlo.ingest")

    assert ingest.assets() == ["dlt_asset", "sling_asset"]
    assert discovery_calls == 1
