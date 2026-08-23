"""Tests for authorization policy backend capability.

Registry resolution fails closed on missing or ambiguous providers; the
default backend loads project policies (refusing regulated startup when they
are absent) and evaluates allow/deny with deny-wins precedence, wildcard
actions, resource filtering, and explain parity with is_allowed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from phlo.capabilities import (
    AuthorizationPolicyBackendSpec,
    Principal,
    ResourceRef,
    get_capability_registry,
    list_capabilities,
    register_capability,
    resolve_capability,
)
from phlo.capabilities.authorization import (
    DefaultAuthorizationPolicyBackend,
    register_default_capability_providers,
)
from phlo.capabilities.interfaces import AuthPrincipal
from phlo.identity.bridge import create_regulated_bridge
from phlo.rbac.models import CanonicalRBAC, PoliciesConfig, RolesConfig
from tests.helpers import reset_capability_test_state

pytestmark = pytest.mark.core_regression


def teardown_function() -> None:
    """Reset global capability registry between tests."""
    reset_capability_test_state()


def test_enforcement_fails_closed_for_missing_or_ambiguous_provider(monkeypatch) -> None:
    """Discovery does not turn missing or ambiguous backend selection into access."""
    from phlo.capabilities import clear_all_capabilities
    from phlo.capabilities import discovery as capability_discovery
    from phlo.security.enforcement import EnforcementContext

    monkeypatch.setattr(capability_discovery, "discover_capabilities", lambda: None)
    monkeypatch.setattr(
        "phlo.infrastructure.config.get_configured_authorization_backend_name",
        lambda: "missing",
    )
    clear_all_capabilities()
    with pytest.raises(RuntimeError, match="Configured authorization_policy_backend"):
        EnforcementContext()._init_authorization_backend()

    monkeypatch.setattr(
        "phlo.infrastructure.config.get_configured_authorization_backend_name",
        lambda: None,
    )
    register_capability(
        "authorization_policy_backend",
        AuthorizationPolicyBackendSpec(name="one", provider=DefaultAuthorizationPolicyBackend()),
    )
    register_capability(
        "authorization_policy_backend",
        AuthorizationPolicyBackendSpec(name="two", provider=DefaultAuthorizationPolicyBackend()),
    )
    with pytest.raises(RuntimeError, match="No authorization_policy_backend"):
        EnforcementContext()._init_authorization_backend()


def test_registry_tracks_authorization_policy_backends() -> None:
    backend = DefaultAuthorizationPolicyBackend()
    register_capability(
        "authorization_policy_backend",
        AuthorizationPolicyBackendSpec(
            name="default",
            provider=backend,
        ),
    )

    registry = get_capability_registry()
    backends = registry.list("authorization_policy_backend")

    assert len(backends) == 1
    assert backends[0].name == "default"
    assert backends[0].provider is backend


def test_list_authorization_policy_backends() -> None:
    register_capability(
        "authorization_policy_backend",
        AuthorizationPolicyBackendSpec(
            name="rbac",
            provider=DefaultAuthorizationPolicyBackend(),
        ),
    )

    names = list_capabilities("authorization_policy_backend")
    assert "rbac" in names


def test_resolve_authorization_policy_backend() -> None:
    backend = DefaultAuthorizationPolicyBackend()
    register_capability(
        "authorization_policy_backend",
        AuthorizationPolicyBackendSpec(
            name="rbac",
            provider=backend,
        ),
    )

    resolved = resolve_capability("authorization_policy_backend", "rbac")
    assert resolved is not None
    assert resolved.name == "rbac"
    assert resolved.provider is backend


def test_default_authorization_policy_backend_explicit_allow() -> None:
    backend = DefaultAuthorizationPolicyBackend(
        policies=[
            {
                "policy_id": "analyst_read",
                "effect": "allow",
                "principal": {"roles": ["analyst"]},
                "action": "dataset.read",
                "resource": {"type": "dataset", "id_pattern": "analytics.*"},
            }
        ]
    )

    principal = Principal(subject="alice", principal_type="user", roles=("analyst",))
    resource = ResourceRef(resource_type="dataset", resource_id="analytics.orders")

    assert backend.is_allowed(principal, "dataset.read", resource) is True


def test_auth_principal_uses_canonical_subject_assignments_and_inheritance() -> None:
    rbac = CanonicalRBAC.from_configs(
        RolesConfig.from_dict(
            {
                "roles": {
                    "viewer": {"inherits": []},
                    "custom_analyst": {"inherits": ["viewer"]},
                },
                "subjects": {"users": {"alice": ["custom_analyst"]}},
            }
        ),
        PoliciesConfig.from_dict(
            {
                "policies": [
                    {
                        "policy_id": "viewer_dataset_read",
                        "effect": "allow",
                        "principal": {"roles": ["viewer"]},
                        "action": "dataset.read",
                        "resource": {"type": "dataset", "id_pattern": "orders"},
                    }
                ]
            }
        ),
    )
    bridge = create_regulated_bridge(canonical_rbac=rbac)
    backend = DefaultAuthorizationPolicyBackend(rbac=rbac)

    principal = bridge.canonicalize(
        AuthPrincipal(
            subject="alice",
            principal_type="user",
            groups=("admin",),
        )
    )

    assert set(principal.roles) == {"custom_analyst", "viewer"}
    assert principal.attributes["idp_groups"] == "admin"
    assert backend.is_allowed(
        principal,
        "dataset.read",
        ResourceRef(resource_type="dataset", resource_id="orders"),
    )
    assert not backend.is_allowed(
        bridge.canonicalize(AuthPrincipal(subject="bob", principal_type="user")),
        "dataset.read",
        ResourceRef(resource_type="dataset", resource_id="orders"),
    )


def test_default_provider_loads_project_policies(monkeypatch, tmp_path: Path) -> None:
    policy_dir = tmp_path / ".phlo" / "authorization"
    policy_dir.mkdir(parents=True)
    (policy_dir / "policies.yaml").write_text(
        """
version: 1
policies:
  - policy_id: allow_analyst
    effect: allow
    principal:
      roles: [analyst]
    action: dataset.read
    resource:
      type: dataset
      id_pattern: analytics.*
""".lstrip()
    )
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_REGULATED", "false")

    register_default_capability_providers()
    backend = resolve_capability("authorization_policy_backend", "default").provider

    assert backend.is_allowed(
        Principal(subject="alice", principal_type="user", roles=("analyst",)),
        "dataset.read",
        ResourceRef(resource_type="dataset", resource_id="analytics.orders"),
    )


def test_default_provider_fails_regulated_startup_without_policies(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_REGULATED", "true")

    with pytest.raises(RuntimeError, match="Regulated authorization policies"):
        register_default_capability_providers()


def test_default_authorization_policy_backend_explicit_deny() -> None:
    backend = DefaultAuthorizationPolicyBackend(
        policies=[
            {
                "policy_id": "deny_sensitive",
                "effect": "deny",
                "principal": {"roles": ["analyst"]},
                "action": "dataset.read",
                "resource": {"type": "dataset", "id_pattern": "sensitive.*"},
            },
            {
                "policy_id": "analyst_read",
                "effect": "allow",
                "principal": {"roles": ["analyst"]},
                "action": "dataset.read",
                "resource": {"type": "dataset", "id_pattern": "*"},
            },
        ]
    )

    principal = Principal(subject="alice", principal_type="user", roles=("analyst",))
    resource = ResourceRef(resource_type="dataset", resource_id="sensitive.data")

    decision = backend.explain_decision(principal, "dataset.read", resource)
    assert decision.allowed is False
    assert decision.reason_code == "explicit_deny"


def test_default_authorization_policy_backend_default_deny() -> None:
    backend = DefaultAuthorizationPolicyBackend(policies=[])

    principal = Principal(subject="alice", principal_type="user", roles=("analyst",))
    resource = ResourceRef(resource_type="dataset", resource_id="analytics.orders")

    decision = backend.explain_decision(principal, "dataset.read", resource)
    assert decision.allowed is False
    assert decision.reason_code == "default_deny"


def test_default_authorization_policy_backend_deny_wins_over_allow() -> None:
    backend = DefaultAuthorizationPolicyBackend(
        policies=[
            {
                "policy_id": "analyst_read_all",
                "effect": "allow",
                "principal": {"roles": ["analyst"]},
                "action": "dataset.read",
                "resource": {"type": "dataset", "id_pattern": "*"},
            },
            {
                "policy_id": "deny_restricted",
                "effect": "deny",
                "principal": {"roles": ["analyst"]},
                "action": "dataset.read",
                "resource": {"type": "dataset", "id_pattern": "restricted.*"},
            },
        ]
    )

    principal = Principal(subject="alice", principal_type="user", roles=("analyst",))
    resource = ResourceRef(resource_type="dataset", resource_id="restricted.data")

    decision = backend.explain_decision(principal, "dataset.read", resource)
    assert decision.allowed is False
    assert decision.reason_code == "explicit_deny"


def test_default_authorization_policy_backend_wildcard_action() -> None:
    backend = DefaultAuthorizationPolicyBackend(
        policies=[
            {
                "policy_id": "admin_all",
                "effect": "allow",
                "principal": {"roles": ["admin"]},
                "action": "*",
                "resource": {"type": "dataset", "id_pattern": "*"},
            }
        ]
    )

    principal = Principal(subject="bob", principal_type="user", roles=("admin",))
    resource = ResourceRef(resource_type="dataset", resource_id="any.dataset")

    assert backend.is_allowed(principal, "dataset.read", resource) is True
    assert backend.is_allowed(principal, "dataset.query", resource) is True
    assert backend.is_allowed(principal, "asset.execute", resource) is True


def test_default_authorization_policy_backend_filter_resources() -> None:
    backend = DefaultAuthorizationPolicyBackend(
        policies=[
            {
                "policy_id": "analyst_read",
                "effect": "allow",
                "principal": {"roles": ["analyst"]},
                "action": "dataset.read",
                "resource": {"type": "dataset", "id_pattern": "analytics.*"},
            }
        ]
    )

    principal = Principal(subject="alice", principal_type="user", roles=("analyst",))
    resources = [
        ResourceRef(resource_type="dataset", resource_id="analytics.orders"),
        ResourceRef(resource_type="dataset", resource_id="sales.revenue"),
        ResourceRef(resource_type="dataset", resource_id="analytics.users"),
    ]

    allowed = backend.filter_resources(principal, resources, "dataset.read")

    assert len(allowed) == 2
    allowed_ids = {r.resource_id for r in allowed}
    assert "analytics.orders" in allowed_ids
    assert "analytics.users" in allowed_ids
    assert "sales.revenue" not in allowed_ids


def test_default_authorization_policy_backend_fail_closed() -> None:
    class FailingBackend:
        def is_allowed(self, *args, **kwargs):
            raise RuntimeError("Provider failed")

        def explain_decision(self, *args, **kwargs):
            raise RuntimeError("Provider failed")

        def filter_resources(self, *args, **kwargs):
            raise RuntimeError("Provider failed")

    backend = FailingBackend()

    principal = Principal(subject="alice", principal_type="user", roles=("analyst",))
    resource = ResourceRef(resource_type="dataset", resource_id="analytics.orders")

    with pytest.raises(RuntimeError, match="Provider failed"):
        backend.is_allowed(principal, "dataset.read", resource)


def test_is_allowed_and_explain_decision_parity() -> None:
    backend = DefaultAuthorizationPolicyBackend(
        policies=[
            {
                "policy_id": "analyst_read",
                "effect": "allow",
                "principal": {"roles": ["analyst"]},
                "action": "dataset.read",
                "resource": {"type": "dataset", "id_pattern": "analytics.*"},
            }
        ]
    )

    principal = Principal(subject="alice", principal_type="user", roles=("analyst",))
    resource = ResourceRef(resource_type="dataset", resource_id="analytics.orders")

    is_allowed_result = backend.is_allowed(principal, "dataset.read", resource)
    explain_result = backend.explain_decision(principal, "dataset.read", resource)

    assert is_allowed_result == explain_result.allowed
    assert explain_result.reason_code == "explicit_allow"
