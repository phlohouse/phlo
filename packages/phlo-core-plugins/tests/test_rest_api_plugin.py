"""Tests for the REST API source plugin.

Uses a stubbed ``requests.get`` to verify record extraction from top-level
list payloads and from nested payloads addressed via ``records_path``.
"""

from phlo_core.sources.rest_api import RestAPIPlugin


class DummyResponse:
    """Simple response test double for requests.get."""

    def __init__(self, payload):
        """Store the JSON-serializable payload returned by ``json()``."""
        self._payload = payload

    def raise_for_status(self):
        """Simulate a successful HTTP status response."""
        return None

    def json(self):
        """Return the configured payload."""
        return self._payload


def test_rest_api_plugin_fetches_records(monkeypatch):
    """Verify the plugin yields records from a top-level list payload."""
    plugin = RestAPIPlugin()
    payload = [{"id": 1}, {"id": 2}]

    def dummy_get(url, headers=None, params=None, timeout=30, **kwargs):
        """Return a dummy response for the request call."""
        return DummyResponse(payload)

    monkeypatch.setattr("phlo_core.sources.rest_api.requests.get", dummy_get)
    records = list(plugin.fetch_data({"url": "https://example.com"}))

    assert records == payload


def test_rest_api_plugin_records_path(monkeypatch):
    """Verify the plugin resolves records via a nested records path."""
    plugin = RestAPIPlugin()
    payload = {"data": {"items": [{"id": 3}]}}

    def dummy_get(url, headers=None, params=None, timeout=30, **kwargs):
        """Return a dummy response for the request call."""
        return DummyResponse(payload)

    monkeypatch.setattr("phlo_core.sources.rest_api.requests.get", dummy_get)
    records = list(plugin.fetch_data({"url": "https://example.com", "records_path": "data.items"}))

    assert records == [{"id": 3}]
