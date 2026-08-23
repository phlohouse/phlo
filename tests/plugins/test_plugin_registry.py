"""Tests for plugin registry client.

fetch_registry serves from a TTL-bounded cache, falls back to bundled
local data when no URL is configured or the remote fetch fails, and
raises an explicit error for payloads missing a plugins section.
search_plugins filters by query, type, and tags over the cached
registry. All HTTP is faked.
"""

import time

import httpx
import pytest

from phlo.plugins import registry_client


def test_fetch_registry_uses_local_cache(monkeypatch):
    """Fetch registry falls back to bundled data when URL is empty."""
    sample_registry = {
        "version": "1.0.0",
        "plugins": {
            "example": {
                "type": "source",
                "package": "phlo-plugin-example",
                "version": "1.0.0",
                "description": "Example",
                "author": "Phlo Team",
                "tags": ["example"],
                "verified": True,
            }
        },
    }

    class DummySettings:
        """Settings stub for exercising local-registry fallback path."""

        plugin_registry_url = ""
        plugin_registry_cache_ttl_seconds = 3600
        plugin_registry_timeout_seconds = 1

    registry_client.clear_registry_cache()
    monkeypatch.setattr(registry_client, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(registry_client, "_load_registry_from_local", lambda: sample_registry)

    registry = registry_client.fetch_registry(force_refresh=True)

    assert registry["plugins"]["example"]["package"] == "phlo-plugin-example"


def test_search_plugins_filters(monkeypatch):
    """Search filters by query, type, and tags."""
    sample_registry = {
        "version": "1.0.0",
        "plugins": {
            "alpha": {
                "type": "source",
                "package": "phlo-plugin-alpha",
                "version": "1.0.0",
                "description": "Alpha connector",
                "author": "Phlo Team",
                "tags": ["api", "alpha"],
                "verified": True,
            },
            "beta": {
                "type": "service",
                "package": "phlo-plugin-beta",
                "version": "1.0.0",
                "description": "Beta service",
                "author": "Phlo Team",
                "tags": ["service", "beta"],
                "verified": True,
            },
        },
    }

    class DummySettings:
        """Settings stub for exercising cached registry search behavior."""

        plugin_registry_url = ""
        plugin_registry_cache_ttl_seconds = 3600
        plugin_registry_timeout_seconds = 1

    registry_client.clear_registry_cache()
    monkeypatch.setattr(registry_client, "get_settings", lambda: DummySettings())
    registry_client._REGISTRY_CACHE["data"] = sample_registry
    registry_client._REGISTRY_CACHE["loaded_at"] = time.time()

    results = registry_client.search_plugins(query="alpha")
    assert len(results) == 1
    assert results[0].name == "alpha"

    results = registry_client.search_plugins(plugin_type="service")
    assert len(results) == 1
    assert results[0].name == "beta"

    results = registry_client.search_plugins(tags=["api"])
    assert len(results) == 1
    assert results[0].name == "alpha"


def test_fetch_registry_remote_exception_falls_back_to_local(monkeypatch):
    """Remote fetch failures should fall back to local registry data."""
    sample_registry = {
        "version": "1.0.0",
        "plugins": {
            "fallback": {
                "type": "source",
                "package": "phlo-plugin-fallback",
                "version": "1.0.0",
                "description": "Fallback plugin",
                "author": "Phlo Team",
                "tags": ["fallback"],
                "verified": True,
            }
        },
    }

    class DummySettings:
        plugin_registry_url = "https://example.com/registry.json"
        plugin_registry_cache_ttl_seconds = 3600
        plugin_registry_timeout_seconds = 2

    local_calls = {"count": 0}

    def fake_load_local():
        local_calls["count"] += 1
        return sample_registry

    def fake_get(*_args, **_kwargs):
        raise httpx.TimeoutException("network timeout")

    registry_client.clear_registry_cache()
    monkeypatch.setattr(registry_client, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(registry_client, "_load_registry_from_local", fake_load_local)
    monkeypatch.setattr(registry_client.httpx, "get", fake_get)

    registry = registry_client.fetch_registry(force_refresh=True)

    assert registry["plugins"]["fallback"]["package"] == "phlo-plugin-fallback"
    assert local_calls["count"] == 1


def test_fetch_registry_invalid_payload_raises_explicit_error(monkeypatch):
    """Invalid payload should raise a clear validation error."""

    class DummySettings:
        plugin_registry_url = ""
        plugin_registry_cache_ttl_seconds = 3600
        plugin_registry_timeout_seconds = 1

    registry_client.clear_registry_cache()
    monkeypatch.setattr(registry_client, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(registry_client, "_load_registry_from_local", lambda: {"version": "1.0.0"})

    with pytest.raises(ValueError, match="Registry payload missing plugins section\\."):
        registry_client.fetch_registry(force_refresh=True)


def test_fetch_registry_respects_cache_ttl_and_avoids_extra_http(monkeypatch):
    """Cached registry should skip HTTP until TTL expires."""
    first_registry = {
        "version": "1.0.0",
        "plugins": {
            "alpha": {
                "type": "source",
                "package": "phlo-plugin-alpha",
                "version": "1.0.0",
                "description": "Alpha plugin",
                "author": "Phlo Team",
                "tags": ["alpha"],
                "verified": True,
            }
        },
    }
    second_registry = {
        "version": "1.1.0",
        "plugins": {
            "alpha": {
                "type": "source",
                "package": "phlo-plugin-alpha",
                "version": "2.0.0",
                "description": "Alpha plugin",
                "author": "Phlo Team",
                "tags": ["alpha"],
                "verified": True,
            }
        },
    }

    class DummySettings:
        plugin_registry_url = "https://example.com/registry.json"
        plugin_registry_cache_ttl_seconds = 60
        plugin_registry_timeout_seconds = 2

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    http_calls = []

    def fake_get(url, timeout):
        http_calls.append((url, timeout))
        payload = first_registry if len(http_calls) == 1 else second_registry
        return FakeResponse(payload)

    timestamps = iter([100.0, 120.0, 200.0])

    registry_client.clear_registry_cache()
    monkeypatch.setattr(registry_client, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(registry_client.time, "time", lambda: next(timestamps))
    monkeypatch.setattr(registry_client.httpx, "get", fake_get)

    first_fetch = registry_client.fetch_registry()
    second_fetch = registry_client.fetch_registry()
    third_fetch = registry_client.fetch_registry()

    assert len(http_calls) == 2
    assert first_fetch["plugins"]["alpha"]["version"] == "1.0.0"
    assert second_fetch["plugins"]["alpha"]["version"] == "1.0.0"
    assert third_fetch["plugins"]["alpha"]["version"] == "2.0.0"
