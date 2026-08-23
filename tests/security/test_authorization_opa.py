"""Tests for OPA authorization policy backend.

All OPA responses are stubbed at the HTTP layer. Under test: the
principal/action/resource/context input envelope, bool and dict policy
results mapping to explained allow/deny decisions, connection errors
and timeouts failing closed, resource filtering by decision, and the
health check / provider factory plumbing.
"""

from __future__ import annotations

import httpx
import pytest

from phlo.capabilities.authorization_opa import (
    OPAAuthorizationPolicyBackend,
    create_opa_provider,
)
from phlo.capabilities.interfaces import (
    DecisionContext,
    Principal,
    ResourceRef,
)

pytestmark = pytest.mark.core_regression


@pytest.fixture()
def principal() -> Principal:
    return Principal(subject="alice", principal_type="user", roles=("analyst",))


@pytest.fixture()
def resource() -> ResourceRef:
    return ResourceRef(resource_type="dataset", resource_id="analytics.orders")


@pytest.fixture()
def backend() -> OPAAuthorizationPolicyBackend:
    return OPAAuthorizationPolicyBackend()


def test_init_rejects_invalid_url_scheme() -> None:
    with pytest.raises(ValueError, match="http:// or https://"):
        OPAAuthorizationPolicyBackend(opa_url="ftp://localhost:8181")


def test_init_defaults_to_localhost() -> None:
    b = OPAAuthorizationPolicyBackend()
    assert b._opa_url == "http://localhost:8181"


def test_build_input_structure(
    backend: OPAAuthorizationPolicyBackend,
    principal: Principal,
    resource: ResourceRef,
) -> None:
    ctx = DecisionContext(environment="prod", request_id="r1")
    result = backend._build_input(principal, "dataset.read", resource, ctx)

    assert result["principal"]["subject"] == "alice"
    assert result["principal"]["type"] == "user"
    assert result["principal"]["roles"] == ["analyst"]
    assert result["action"] == "dataset.read"
    assert result["resource"]["type"] == "dataset"
    assert result["resource"]["id"] == "analytics.orders"
    assert result["context"]["environment"] == "prod"
    assert result["context"]["request_id"] == "r1"


def test_explain_decision_allow_bool_result(
    backend: OPAAuthorizationPolicyBackend,
    principal: Principal,
    resource: ResourceRef,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend, "_evaluate", lambda _: {"result": True})
    decision = backend.explain_decision(principal, "dataset.read", resource)
    assert decision.allowed is True
    assert decision.reason_code == "opa_allow"


def test_explain_decision_deny_bool_result(
    backend: OPAAuthorizationPolicyBackend,
    principal: Principal,
    resource: ResourceRef,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend, "_evaluate", lambda _: {"result": False})
    decision = backend.explain_decision(principal, "dataset.read", resource)
    assert decision.allowed is False
    assert decision.reason_code == "opa_deny"


def test_explain_decision_allow_dict_result(
    backend: OPAAuthorizationPolicyBackend,
    principal: Principal,
    resource: ResourceRef,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        backend, "_evaluate", lambda _: {"result": {"allow": True, "reason": "custom"}}
    )
    decision = backend.explain_decision(principal, "dataset.read", resource)
    assert decision.allowed is True
    assert decision.explanation == "custom"


def test_explain_decision_deny_dict_result(
    backend: OPAAuthorizationPolicyBackend,
    principal: Principal,
    resource: ResourceRef,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        backend,
        "_evaluate",
        lambda _: {"result": {"allow": False, "reason_code": "custom_deny"}},
    )
    decision = backend.explain_decision(principal, "dataset.read", resource)
    assert decision.allowed is False
    assert decision.reason_code == "custom_deny"


def test_explain_decision_none_response(
    backend: OPAAuthorizationPolicyBackend,
    principal: Principal,
    resource: ResourceRef,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend, "_evaluate", lambda _: None)
    decision = backend.explain_decision(principal, "dataset.read", resource)
    assert decision.allowed is False
    assert decision.reason_code == "backend_unavailable"


def test_explain_decision_connect_error(
    backend: OPAAuthorizationPolicyBackend,
    principal: Principal,
    resource: ResourceRef,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(_: object) -> None:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(backend, "_evaluate", _raise)
    decision = backend.explain_decision(principal, "dataset.read", resource)
    assert decision.allowed is False
    assert decision.reason_code == "backend_unavailable"


def test_explain_decision_timeout(
    backend: OPAAuthorizationPolicyBackend,
    principal: Principal,
    resource: ResourceRef,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(_: object) -> None:
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(backend, "_evaluate", _raise)
    decision = backend.explain_decision(principal, "dataset.read", resource)
    assert decision.allowed is False
    assert decision.reason_code == "backend_unavailable"


def test_is_allowed_delegates_to_explain_decision(
    backend: OPAAuthorizationPolicyBackend,
    principal: Principal,
    resource: ResourceRef,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend, "_evaluate", lambda _: {"result": True})
    assert backend.is_allowed(principal, "dataset.read", resource) is True

    monkeypatch.setattr(backend, "_evaluate", lambda _: {"result": False})
    assert backend.is_allowed(principal, "dataset.read", resource) is False


def test_filter_resources(
    backend: OPAAuthorizationPolicyBackend,
    principal: Principal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r1 = ResourceRef(resource_type="dataset", resource_id="a")
    r2 = ResourceRef(resource_type="dataset", resource_id="b")
    r3 = ResourceRef(resource_type="dataset", resource_id="c")

    allowed_ids = {"a", "c"}

    def fake_evaluate(input_data: object) -> dict:
        rid = input_data["resource"]["id"]  # type: ignore[index]
        return {"result": rid in allowed_ids}

    monkeypatch.setattr(backend, "_evaluate", fake_evaluate)
    result = backend.filter_resources(principal, [r1, r2, r3], "dataset.read")

    assert len(result) == 2
    assert {r.resource_id for r in result} == {"a", "c"}


def test_health_check_success(
    backend: OPAAuthorizationPolicyBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        status_code = 200

    class FakeClient:
        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def get(self, url: str) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", lambda **kw: FakeClient())
    assert backend.health_check() is True


def test_health_check_failure(
    backend: OPAAuthorizationPolicyBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(**kw: object) -> None:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "Client", _raise)
    assert backend.health_check() is False


def test_create_opa_provider_factory() -> None:
    provider, support = create_opa_provider(opa_url="http://opa:8181")
    assert isinstance(provider, OPAAuthorizationPolicyBackend)
    assert provider._opa_url == "http://opa:8181"
    assert support.supports_permissions is True
    assert support.supports_attributes is True
