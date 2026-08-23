"""Tests for platform principal type support.

Verifies that principal_type='platform' (service daemons) flows through
AuthPrincipal, canonicalization, the regulated identity bridge, and
enforcement with the correct audit actor_type.
"""

from __future__ import annotations

from phlo.capabilities.interfaces import AuthPrincipal
from phlo.identity.bridge import IdentityBridge, IdentityBridgeConfig, canonicalize_principal


def test_auth_principal_accepts_platform_type():
    """AuthPrincipal accepts principal_type='platform'."""
    principal = AuthPrincipal(
        subject="platform:dagster-daemon",
        principal_type="platform",
        groups=("dagster-daemon",),
        attributes={"authentication_source": "daemon"},
    )
    assert principal.subject == "platform:dagster-daemon"
    assert principal.principal_type == "platform"


def test_canonicalize_platform_principal():
    """Platform principal canonicalizes without error."""
    auth = AuthPrincipal(
        subject="platform:dagster-daemon",
        principal_type="platform",
        groups=("dagster-daemon",),
        attributes={"authentication_source": "daemon"},
    )
    principal = canonicalize_principal(auth, regulated=True)
    assert principal.subject == "platform:dagster-daemon"
    assert principal.principal_type == "platform"


def test_regulated_bridge_approves_platform_type():
    """Regulated bridge with enforce_approved_principal_types accepts 'platform'."""
    config = IdentityBridgeConfig(enforce_approved_principal_types=True)
    bridge = IdentityBridge(config)
    auth = AuthPrincipal(
        subject="platform:dagster-daemon",
        principal_type="platform",
        groups=(),
        attributes={"authentication_source": "daemon"},
    )
    principal = bridge.canonicalize(auth)
    assert principal.principal_type == "platform"


def test_enforce_with_platform_principal(monkeypatch):
    """enforce() works with a platform principal and emits correct audit actor_type."""
    from unittest.mock import MagicMock

    from phlo.capabilities.interfaces import ResourceRef
    from phlo.security.enforcement import EnforcementContext, enforce

    monkeypatch.setattr(EnforcementContext, "_instance", None)

    mock_backend = MagicMock()
    mock_backend.explain_decision.return_value = MagicMock(
        allowed=True, reason_code=None, policy_id=None, explanation=None
    )

    mock_emitter = MagicMock()

    ctx = EnforcementContext.get_instance()
    ctx._authorization_backend = mock_backend
    ctx._audit_emitter = mock_emitter
    ctx._initialized = True

    auth = AuthPrincipal(
        subject="platform:dagster-daemon",
        principal_type="platform",
        groups=("dagster-daemon",),
        attributes={"authentication_source": "daemon"},
    )

    result = enforce(
        principal=auth,
        action="run.execute",
        resource=ResourceRef(resource_type="run", resource_id="test-run"),
        surface="dagster-daemon",
    )

    assert result.allowed
    mock_emitter.emit_authorization.assert_called_once()
    call_kwargs = mock_emitter.emit_authorization.call_args.kwargs
    assert call_kwargs["actor_type"] == "platform"
    assert call_kwargs["actor_subject"] == "platform:dagster-daemon"
    assert call_kwargs["surface"] == "dagster-daemon"

    EnforcementContext.reset_instance()
