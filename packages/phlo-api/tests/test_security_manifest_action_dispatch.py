"""Tests that payload-dispatched Observatory actions are authorised against the
action and resource their action_id names, before any handler runs."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from phlo.capabilities.interfaces import AuthorizationDecision, AuthPrincipal, Principal
from phlo.security.adapters import EnforcementResult
from phlo_api import security_manifest
from phlo_api.main import app

ACTIONS = "/api/observatory/actions"
BRANCH_ACTIONS = "/api/observatory/branches/actions"


class _DenyingBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def explain_decision(self, principal, action, resource, context=None):
        self.calls.append((action, resource.resource_type, resource.resource_id))
        return AuthorizationDecision(
            allowed=False, reason_code="default_deny", policy_id=None, explanation="test"
        )


@pytest.fixture
def backend(monkeypatch) -> _DenyingBackend:
    """Authenticate every request as an operator and deny it with a recording backend."""
    denying = _DenyingBackend()
    auth = AuthPrincipal(subject="operator", principal_type="user", groups=())
    canonical = Principal(subject="operator", principal_type="user", roles=("operator",))

    def enforce(*, principal, action, resource, context, **_kwargs):
        denying.explain_decision(canonical, action, resource, context)
        return EnforcementResult.deny(reason_code="default_deny")

    monkeypatch.setattr(security_manifest, "is_regulated", lambda: True)
    monkeypatch.setattr(security_manifest, "enforce", enforce)
    monkeypatch.setattr(security_manifest, "get_request_principal", lambda _request: auth)
    monkeypatch.setattr(
        security_manifest,
        "resolve_request_principal",
        lambda _request, require_auth=True: canonical,
    )
    monkeypatch.setattr(security_manifest, "get_authorization_backend", lambda: denying)
    return denying


def _post(path: str, body: object):
    return TestClient(app).post(path, json=body, headers={"Authorization": "Bearer operator"})


@pytest.mark.parametrize(
    ("action_id", "expected"),
    [
        ("dataset:orders:publish", ("dataset.publish", "dataset", "dataset_id=orders")),
        ("dataset:raw.orders:retire", ("dataset.write", "dataset", "dataset_id=raw.orders")),
        ("candidate:tbl-1:claim", ("dataset.write", "dataset", "table_id=tbl-1")),
        ("candidate:tbl-1:promote", ("dataset.write", "dataset", "table_id=tbl-1")),
        ("candidate:tbl-1:reject", ("dataset.write", "dataset", "table_id=tbl-1")),
        ("asset:raw/orders:materialize", ("asset.execute", "asset", "asset_id=raw/orders")),
        ("branch:delete:feature/x", ("catalog.manage", "catalog", "branch_name=feature/x")),
        ("branch:create: dev ", ("catalog.manage", "catalog", "branch_name=dev")),
        ("workflow:wf-1:run", ("object.write", "object", "workflow_id=wf-1")),
        ("service:trino:restart", ("service.manage", "service", "service_id=trino")),
        ("trino:restart", ("service.manage", "service", "service_id=trino")),
        ("minio:add", ("service.manage", "service", "service_id=minio")),
        ("trino:stop", ("service.manage", "service", "service_id=trino")),
        ("trino:start", ("service.manage", "service", "service_id=trino")),
        ("cache:flush", ("admin.manage", "admin", "action_id=cache:flush")),
        ("reindex", ("admin.manage", "admin", "action_id=reindex")),
    ],
)
def test_action_id_selects_the_authorised_action_and_resource(
    backend, action_id: str, expected: tuple[str, str, str]
) -> None:
    response = _post(ACTIONS, {"action_id": action_id})

    assert response.status_code == 403
    assert backend.calls == [expected]


@pytest.mark.parametrize(
    "action_id",
    [
        "dataset:orders",
        "dataset::publish",
        "dataset:orders:delete",
        "candidate:tbl-1",
        "candidate:tbl-1:publish",
        "asset:materialize",
        "asset::run",
        "branch:delete",
        "branch:delete:  ",
        "workflow:run",
        "service:restart",
    ],
)
def test_malformed_action_ids_are_rejected_before_authorisation(backend, action_id: str) -> None:
    response = _post(ACTIONS, {"action_id": action_id})

    assert response.status_code == 400
    assert response.json() == {"error": "invalid_action"}
    assert backend.calls == []


def test_body_identity_matching_the_action_id_is_accepted(backend) -> None:
    response = _post(ACTIONS, {"action_id": "dataset:orders:publish", "dataset_id": "orders"})

    assert response.status_code == 403
    assert backend.calls == [("dataset.publish", "dataset", "dataset_id=orders")]


@pytest.mark.parametrize(
    "body",
    [
        {"action_id": "dataset:orders:publish", "dataset_id": "customers"},
        {"action_id": "service:trino:restart", "service_id": "minio"},
        {"action_id": "candidate:tbl-1:claim", "table_id": 2},
    ],
)
def test_body_identity_conflicting_with_the_action_id_is_ambiguous(backend, body) -> None:
    response = _post(ACTIONS, body)

    assert response.status_code == 400
    assert response.json() == {"error": "ambiguous_resource"}
    assert backend.calls == []


@pytest.mark.parametrize(
    ("action_id", "expected"),
    [
        ("branch:delete:payments", ("catalog.manage", "catalog", "branch_name=payments")),
        ("branch:merge: feature ", ("catalog.manage", "catalog", "branch_name=feature")),
    ],
)
def test_branch_actions_bind_the_branch_named_by_the_action_id(
    backend, action_id: str, expected: tuple[str, str, str]
) -> None:
    response = _post(BRANCH_ACTIONS, {"action_id": action_id})

    assert response.status_code == 403
    assert backend.calls == [expected]


@pytest.mark.parametrize(
    "action_id",
    ["dataset:orders:publish", "branch:delete", "branch:delete:  ", "branches:delete:x"],
)
def test_branch_actions_reject_non_branch_action_ids(backend, action_id: str) -> None:
    response = _post(BRANCH_ACTIONS, {"action_id": action_id})

    assert response.status_code == 400
    assert response.json() == {"error": "invalid_action"}
    assert backend.calls == []


def test_branch_action_conflicting_branch_name_is_ambiguous(backend) -> None:
    response = _post(BRANCH_ACTIONS, {"action_id": "branch:delete:payments", "branch_name": "x"})

    assert response.status_code == 400
    assert response.json() == {"error": "ambiguous_resource"}
    assert backend.calls == []


@pytest.mark.parametrize("body", [{}, {"action_id": ""}, ["dataset:x:publish"]])
def test_requests_without_an_action_id_are_missing_their_resource(backend, body) -> None:
    response = _post(ACTIONS, body)

    assert response.status_code == 400
    assert response.json() == {"error": "missing_resource"}
    assert backend.calls == []


def test_non_string_action_id_keeps_the_route_authorisation(backend) -> None:
    response = _post(ACTIONS, {"action_id": 7})

    assert response.status_code == 403
    assert backend.calls == [("service.manage", "service", "action_id=7")]
