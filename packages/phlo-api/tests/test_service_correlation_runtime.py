"""Runtime tests for service identity and correlation propagation.

Verifies that the request correlation id lands in request state, that
downstream service headers carry the caller's principal and correlation id,
and that Trino headers carry user, role, catalog/schema, and correlation id.
"""

from __future__ import annotations

from types import SimpleNamespace

from phlo.capabilities.interfaces import AuthPrincipal
from phlo.logging import bind_context, clear_context
from phlo_api.api.authorization import (
    build_downstream_service_headers,
    get_request_correlation_id,
)
from phlo_api.observatory_api.trino import _build_trino_headers


def test_get_request_correlation_id_sets_request_state():
    request = SimpleNamespace(headers={}, state=SimpleNamespace())

    correlation_id = get_request_correlation_id(request)

    assert correlation_id
    assert request.state.request_id == correlation_id


def test_build_downstream_service_headers_uses_request_identity(monkeypatch):
    monkeypatch.setenv("PHLO_SERVICE_SECRET", "test-secret")
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(request_id="req-123"),
    )
    auth_principal = AuthPrincipal(
        subject="alice@example.com",
        principal_type="user",
        groups=(),
        attributes={"authentication_source": "proxy"},
    )
    monkeypatch.setattr(
        "phlo_api.api.authorization.get_request_principal",
        lambda _request: auth_principal,
    )

    headers = build_downstream_service_headers(request, "phlo-api")

    assert headers["Authorization"].startswith("Bearer phlo-api:")
    assert headers["X-Phlo-Initiator"] == "alice@example.com"
    assert headers["X-Phlo-Correlation-Id"] == "req-123"


def test_build_trino_headers_include_role_and_correlation(monkeypatch):
    monkeypatch.setenv("TRINO_USER", "phlo-api")
    monkeypatch.setenv("TRINO_ROLE", "phlo_api_reader")
    bind_context(request_id="corr-789")
    try:
        headers = _build_trino_headers(catalog="iceberg", schema="gold")
    finally:
        clear_context()

    assert headers["X-Trino-User"] == "phlo-api"
    assert headers["X-Trino-Role"] == "system=phlo_api_reader"
    assert headers["X-Trino-Extra-Credential"] == "phlo.correlation_id=corr-789"
    assert headers["X-Trino-Catalog"] == "iceberg"
    assert headers["X-Trino-Schema"] == "gold"
