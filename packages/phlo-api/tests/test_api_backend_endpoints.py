"""Tests for capability-backed API backend endpoints.

Uses a stubbed authenticated viewer client; no real backends run. The
key guarantee under test: one failing provider is reported unhealthy
instead of breaking discovery for every other backend.
"""

from __future__ import annotations

from security_test_support import authenticated_client


def test_api_backends_endpoint_returns_capability_payload(monkeypatch) -> None:
    """The API should expose capability-backed backend discovery."""
    import phlo_api.main as main_module

    payload = [
        {
            "name": "hasura",
            "healthy": True,
            "metadata": {"backend_kind": "graphql"},
            "description": {"service_name": "hasura"},
        }
    ]
    monkeypatch.setattr(main_module, "_list_api_backends", lambda: payload)

    client = authenticated_client("viewer")
    response = client.get("/api/backends")

    assert response.status_code == 200
    assert response.json() == payload


def test_api_backend_endpoint_returns_404_for_unknown_backend(monkeypatch) -> None:
    """Unknown API backend names should return 404."""
    import phlo_api.main as main_module

    monkeypatch.setattr(main_module, "_get_api_backend", lambda _name: None)

    client = authenticated_client("viewer")
    response = client.get("/api/backends/missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "API backend not found: missing"


def test_list_api_backends_marks_failing_provider_unhealthy(monkeypatch) -> None:
    """One failing provider should not break backend discovery for all providers."""
    import phlo.capabilities as capabilities_module
    import phlo.capabilities.discovery as discovery_module
    import phlo_api.main as main_module

    class HealthyProvider:
        def describe(self) -> dict[str, str]:
            return {"service_name": "healthy"}

        def health_check(self) -> bool:
            return True

    class FailingProvider:
        def describe(self) -> dict[str, str]:
            raise RuntimeError("boom")

        def health_check(self) -> bool:
            raise AssertionError("health_check should not be called after describe failure")

    class FakeSpec:
        def __init__(self, name: str, provider: object) -> None:
            self.name = name
            self.provider = provider
            self.metadata = {"backend_kind": "test"}

    class FakeRegistry:
        def list(self, family: str) -> list[FakeSpec]:
            assert family == "api_backend"
            return [
                FakeSpec("healthy", HealthyProvider()),
                FakeSpec("failing", FailingProvider()),
            ]

    monkeypatch.setattr(capabilities_module, "get_capability_registry", lambda: FakeRegistry())
    monkeypatch.setattr(discovery_module, "discover_capabilities", lambda: None)

    backends = main_module._list_api_backends()

    assert backends == [
        {
            "name": "healthy",
            "healthy": True,
            "metadata": {"backend_kind": "test"},
            "description": {"service_name": "healthy"},
        },
        {
            "name": "failing",
            "healthy": False,
            "metadata": {"backend_kind": "test"},
            "description": None,
        },
    ]
