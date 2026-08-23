"""Explicit authenticated clients for API contract tests.

The production app has no test principal.  These helpers install a tiny
request-header authentication provider and a finite role policy only for the
duration of each client request, so tests must name the principal they intend
to exercise.
"""

from __future__ import annotations

import os
from typing import Any, Literal

import pytest
from fastapi.testclient import TestClient

from phlo.capabilities import (
    AuthPrincipal,
    AuthenticationProviderSpec,
    AuthorizationPolicyBackendSpec,
    clear_capabilities,
    get_capability_registry,
    register_capability,
)
from phlo.capabilities.authorization import DefaultAuthorizationPolicyBackend
from phlo.capabilities.interfaces import Principal, RequestContext
from phlo.rbac.models import CanonicalAction
from phlo.security.adapters import EnforcementResult
from phlo_api.main import app

PrincipalName = Literal["viewer", "analyst", "operator", "admin"]

_READ_ACTIONS = {
    CanonicalAction.DATASET_READ.value,
    CanonicalAction.ASSET_READ.value,
    CanonicalAction.SERVICE_READ.value,
    CanonicalAction.SETTINGS_READ.value,
    CanonicalAction.OBJECT_READ.value,
    CanonicalAction.CATALOG_READ.value,
    CanonicalAction.PLATFORM_METADATA_READ.value,
    CanonicalAction.OBSERVABILITY_READ.value,
    CanonicalAction.MAINTENANCE_READ.value,
    CanonicalAction.RUN_READ.value,
    CanonicalAction.AUDIT_READ.value,
}
_ANALYST_ACTIONS = _READ_ACTIONS | {CanonicalAction.DATASET_QUERY.value}
_OPERATOR_ACTIONS = _ANALYST_ACTIONS | {
    CanonicalAction.DATASET_WRITE.value,
    CanonicalAction.DATASET_PUBLISH.value,
    CanonicalAction.ASSET_EXECUTE.value,
    CanonicalAction.SERVICE_MANAGE.value,
    CanonicalAction.SETTINGS_MANAGE.value,
    CanonicalAction.OBJECT_WRITE.value,
    CanonicalAction.CATALOG_MANAGE.value,
    CanonicalAction.RUN_EXECUTE.value,
    CanonicalAction.RUN_MANAGE.value,
}
_ADMIN_ACTIONS = _OPERATOR_ACTIONS | {
    CanonicalAction.ADMIN_READ.value,
    CanonicalAction.ADMIN_MANAGE.value,
}
_ACTIONS_BY_PRINCIPAL: dict[PrincipalName, set[str]] = {
    "viewer": _READ_ACTIONS,
    "analyst": _ANALYST_ACTIONS,
    "operator": _OPERATOR_ACTIONS,
    "admin": _ADMIN_ACTIONS,
}
_SCOPES_BY_PRINCIPAL: dict[PrincipalName, tuple[str, ...]] = {
    "viewer": ("project:read",),
    "analyst": ("project:read", "lakehouse:read", "lakehouse:query"),
    "operator": ("project:read", "project:write", "lakehouse:read", "lakehouse:operate"),
    "admin": ("admin", "project:read", "project:write", "lakehouse:read", "lakehouse:operate"),
}


class _HeaderAuthenticationProvider:
    def current_principal(self, request: RequestContext) -> AuthPrincipal | None:
        name = request.headers.get("x-test-principal")
        if name not in _ACTIONS_BY_PRINCIPAL:
            return None
        return AuthPrincipal(
            subject=request.headers.get("x-test-subject") or f"test:{name}",
            principal_type="user",
            groups=(f"{name}s" if name != "admin" else "admin",),
            claims={"scopes": list(_SCOPES_BY_PRINCIPAL[name])},
            attributes={"test_principal": name},
        )


def _backend() -> DefaultAuthorizationPolicyBackend:
    policies: list[dict[str, Any]] = []
    for role, actions in _ACTIONS_BY_PRINCIPAL.items():
        for action in actions:
            policies.append(
                {
                    "policy_id": f"test-{role}-{action}",
                    "effect": "allow",
                    "principal": {"roles": [role]},
                    "action": action,
                    "resource": {"type": "*", "id_pattern": "*"},
                }
            )
    return DefaultAuthorizationPolicyBackend(policies=policies)


class _AuthenticatedTestClient(TestClient):
    def __init__(self, principal: PrincipalName) -> None:
        super().__init__(app, headers={"X-Test-Principal": principal})

    def request(self, *args: Any, **kwargs: Any):  # noqa: ANN201
        registry = get_capability_registry()
        previous_auth = registry.list("authentication_provider")
        previous_backend = registry.list("authorization_policy_backend")
        previous_provider_name = os.environ.get("PHLO_AUTHENTICATION_PROVIDER")
        previous_backend_name = os.environ.get("PHLO_AUTHORIZATION_BACKEND")
        # Install the test providers for this request only, then restore the
        # previous registrations and env names. Swapping per request keeps tests
        # that exercise real providers isolated from these overrides.
        register_capability(
            "authentication_provider",
            AuthenticationProviderSpec(
                name="test-client", provider=_HeaderAuthenticationProvider()
            ),
        )
        register_capability(
            "authorization_policy_backend",
            AuthorizationPolicyBackendSpec(name="test-client", provider=_backend()),
        )
        os.environ["PHLO_AUTHENTICATION_PROVIDER"] = "test-client"
        os.environ["PHLO_AUTHORIZATION_BACKEND"] = "test-client"
        try:
            return super().request(*args, **kwargs)
        finally:
            clear_capabilities("authentication_provider")
            clear_capabilities("authorization_policy_backend")
            for spec in previous_auth:
                register_capability("authentication_provider", spec)
            for spec in previous_backend:
                register_capability("authorization_policy_backend", spec)
            if previous_provider_name is None:
                os.environ.pop("PHLO_AUTHENTICATION_PROVIDER", None)
            else:
                os.environ["PHLO_AUTHENTICATION_PROVIDER"] = previous_provider_name
            if previous_backend_name is None:
                os.environ.pop("PHLO_AUTHORIZATION_BACKEND", None)
            else:
                os.environ["PHLO_AUTHORIZATION_BACKEND"] = previous_backend_name


def authenticated_client(principal: PrincipalName) -> TestClient:
    """Create a client with an explicitly named narrow test principal."""
    return _AuthenticatedTestClient(principal)


@pytest.fixture(name="regulated_api_boundary")
def _regulated_api_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run API authorization expectations through an explicit regulated boundary."""
    from phlo_api import security_manifest

    monkeypatch.setenv("PHLO_REGULATED", "true")
    monkeypatch.setattr(security_manifest, "is_regulated", lambda: True)

    def enforce_with_test_backend(*, principal, action, resource, context, **_kwargs):  # noqa: ANN001
        backend = security_manifest.get_authorization_backend()
        if backend is None:
            return EnforcementResult.error(reason_code="backend_unavailable")
        test_role = principal.attributes.get("test_principal")
        decision = backend.explain_decision(
            Principal(
                subject=principal.subject,
                principal_type=principal.principal_type,
                roles=(test_role,) if isinstance(test_role, str) else principal.groups,
                attributes=principal.attributes,
            ),
            action,
            resource,
            context,
        )
        if decision.allowed:
            return EnforcementResult.allow()
        return EnforcementResult.deny(reason_code=decision.reason_code)

    monkeypatch.setattr(security_manifest, "enforce", enforce_with_test_backend)
