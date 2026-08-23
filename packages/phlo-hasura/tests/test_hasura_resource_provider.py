"""Tests for Hasura API backend capability registration.

Verifies that HasuraResourceProvider exposes exactly one "hasura" API
backend, marked as GraphQL, with health-check and describe entry points.
"""

from __future__ import annotations

from phlo_hasura.resource_provider import HasuraResourceProvider


def test_hasura_resource_provider_exposes_api_backend(monkeypatch) -> None:
    """Hasura should register a swappable API backend capability."""
    monkeypatch.setenv("HASURA_ADMIN_SECRET", "test-secret")
    provider = HasuraResourceProvider()

    specs = provider.get_api_backends()

    assert [spec.name for spec in specs] == ["hasura"]
    assert specs[0].metadata["backend_kind"] == "graphql"
    assert hasattr(specs[0].provider, "health_check")
    assert hasattr(specs[0].provider, "describe")
