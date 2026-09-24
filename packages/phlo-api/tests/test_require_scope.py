"""Scope enforcement on phlo-api mutations across authorization modes.

`require_scope` must hand out the development principal only in pure local
development: authorization mode ``optional`` and no production/regulated HTTP
authorization requirement (ADR 0047 decision 2). Every other combination
resolves the request principal and enforces the scope (issue #981).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from phlo_api.api.operation_controls import require_scope
from phlo_api.main import app
from security_test_support import authenticated_client

_PROD_LIKE_ENVIRONMENTS = ("prod", "production", "staging", "regulated")
_BRANCH_ACTION_URL = "/api/observatory/branches/actions"
_BRANCH_ACTION_BODY = {"action_id": "branch:create:inventory-test"}


def _apply_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    *,
    environment: str | None = None,
    regulated: str | None = None,
    authorization_mode: str | None = None,
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    for var, value in (
        ("PHLO_ENVIRONMENT", environment),
        ("PHLO_REGULATED", regulated),
        ("PHLO_AUTHORIZATION_MODE", authorization_mode),
    ):
        if value is None:
            monkeypatch.delenv(var, raising=False)
        else:
            monkeypatch.setenv(var, value)


def _authorization_is_required(
    environment: str | None, regulated: str | None, authorization_mode: str | None
) -> bool:
    if regulated == "true":
        return True
    if environment in _PROD_LIKE_ENVIRONMENTS:
        return True
    return authorization_mode == "required"


def _anonymous_request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/observatory/branches/actions",
            "headers": headers or [],
        }
    )


@pytest.mark.parametrize("environment", [None, "development", "production"])
@pytest.mark.parametrize("regulated", [None, "false", "true"])
@pytest.mark.parametrize("authorization_mode", [None, "optional", "required"])
def test_anonymous_mutation_requires_authorization_when_required(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    environment: str | None,
    regulated: str | None,
    authorization_mode: str | None,
) -> None:
    """Anonymous mutation calls are 401 whenever authorization is required."""
    _apply_env(
        monkeypatch,
        tmp_path,
        environment=environment,
        regulated=regulated,
        authorization_mode=authorization_mode,
    )

    response = TestClient(app).post(_BRANCH_ACTION_URL, json=_BRANCH_ACTION_BODY)

    if _authorization_is_required(environment, regulated, authorization_mode):
        assert response.status_code == 401
    else:
        assert response.status_code == 200


@pytest.mark.parametrize(
    ("environment", "regulated", "authorization_mode"),
    [
        ("production", "false", None),
        ("prod", None, None),
        (None, None, "required"),
        (None, "true", None),
    ],
)
def test_require_scope_rejects_anonymous_when_required(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    environment: str | None,
    regulated: str | None,
    authorization_mode: str | None,
) -> None:
    """require_scope itself raises 401 (no middleware in the call path)."""
    _apply_env(
        monkeypatch,
        tmp_path,
        environment=environment,
        regulated=regulated,
        authorization_mode=authorization_mode,
    )

    with pytest.raises(HTTPException) as excinfo:
        require_scope(_anonymous_request(), "lakehouse:operate")
    assert excinfo.value.status_code == 401


@pytest.mark.parametrize("environment", [None, "development"])
@pytest.mark.parametrize("regulated", [None, "false"])
@pytest.mark.parametrize("authorization_mode", [None, "optional"])
def test_require_scope_returns_dev_principal_in_local_development(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    environment: str | None,
    regulated: str | None,
    authorization_mode: str | None,
) -> None:
    """Pure local development still resolves the development principal."""
    _apply_env(
        monkeypatch,
        tmp_path,
        environment=environment,
        regulated=regulated,
        authorization_mode=authorization_mode,
    )

    principal = require_scope(_anonymous_request(), "admin")
    assert principal == {"subject": "development:anonymous", "scopes": ["admin"]}


def test_require_scope_insufficient_token_scope_is_403(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A bearer token without the required scope gets 403, not 401."""
    _apply_env(monkeypatch, tmp_path, authorization_mode="required")
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"read-token":{"subject":"reader","scopes":["lakehouse:read"]}}',
    )
    request = _anonymous_request([(b"authorization", b"Bearer read-token")])

    with pytest.raises(HTTPException) as excinfo:
        require_scope(request, "lakehouse:operate")
    assert excinfo.value.status_code == 403


def test_require_scope_accepts_token_with_scope(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A bearer token carrying the required scope resolves its principal."""
    _apply_env(monkeypatch, tmp_path, authorization_mode="required")
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"ops-token":{"subject":"operator","scopes":["lakehouse:operate"]}}',
    )
    request = _anonymous_request([(b"authorization", b"Bearer ops-token")])

    principal = require_scope(request, "lakehouse:operate")
    assert principal["subject"] == "operator"


def test_branch_action_route_enforces_lakehouse_operate_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """POST /branches/actions: 401 anonymous, 403 analyst, allowed operator."""
    _apply_env(monkeypatch, tmp_path, authorization_mode="required")

    anonymous = TestClient(app).post(_BRANCH_ACTION_URL, json=_BRANCH_ACTION_BODY)
    assert anonymous.status_code == 401

    analyst = authenticated_client("analyst").post(_BRANCH_ACTION_URL, json=_BRANCH_ACTION_BODY)
    assert analyst.status_code == 403

    operator = authenticated_client("operator").post(_BRANCH_ACTION_URL, json=_BRANCH_ACTION_BODY)
    assert operator.status_code == 200


def test_extension_settings_route_enforces_admin_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """PUT /extensions/{name}/settings: 401 anonymous, 403 without admin."""
    _apply_env(monkeypatch, tmp_path, authorization_mode="required")
    url = "/api/observatory/extensions/demo/settings"

    anonymous = TestClient(app).put(url, json={"settings": {}})
    assert anonymous.status_code == 401

    operator = authenticated_client("operator").put(url, json={"settings": {}})
    assert operator.status_code == 403

    admin = authenticated_client("admin").put(url, json={"settings": {}})
    assert admin.status_code not in (401, 403)


def test_observatory_action_route_rejects_anonymous(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """POST /actions reaches its dispatch only behind lakehouse:operate."""
    _apply_env(monkeypatch, tmp_path, authorization_mode="required")

    anonymous = TestClient(app).post(
        "/api/observatory/actions", json={"action_id": "service:restart:demo"}
    )
    assert anonymous.status_code == 401

    analyst = authenticated_client("analyst").post(
        "/api/observatory/actions", json={"action_id": "service:restart:demo"}
    )
    assert analyst.status_code == 403


def test_saved_query_route_enforces_project_write_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """POST /saved-queries: 401 anonymous, 403 analyst, allowed operator."""
    _apply_env(monkeypatch, tmp_path, authorization_mode="required")
    url = "/api/observatory/saved-queries"
    body = {"name": "probe", "sql": "select 1"}

    anonymous = TestClient(app).post(url, json=body)
    assert anonymous.status_code == 401

    analyst = authenticated_client("analyst").post(url, json=body)
    assert analyst.status_code == 403

    operator = authenticated_client("operator").post(url, json=body)
    assert operator.status_code not in (401, 403)
