"""Core enforcement engine for regulated access.

This module provides the core enforcement path owned by phlo.security.
It is invoked by surface adapters to make authorization decisions.

The EnforcementContext is a process-scoped singleton that owns:
- Identity bridge (for principal canonicalization)
- Authorization policy backend (for PDP decisions)
- Audit event emitter (for canonical audit events)

In regulated mode, the context initializes eagerly at first access.
In non-regulated mode, components are initialized lazily on first use.

Core enforcement must NOT import FastAPI, Click, or Dagster/Starlette types.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from phlo.logging import get_logger
from phlo.security.adapters import EnforcementResult

if TYPE_CHECKING:
    from phlo.capabilities.interfaces import (
        AuthorizationDecision,
        AuthorizationPolicyBackend,
        DecisionContext,
        Principal,
        ResourceRef,
    )
    from phlo.identity.bridge import IdentityBridge

logger = get_logger(__name__)


def _is_regulated() -> bool:
    """Check if regulated mode is active."""
    try:
        from phlo.security.mode import is_regulated

        return is_regulated()
    except Exception:
        return False


class EnforcementContext:
    """Process-scoped singleton for core enforcement.

    Holds the shared infrastructure for all regulated surface enforcement:
    identity bridge, authorization policy backend, and audit emitter.
    Adapters do not hold their own cached instances of these — they use
    EnforcementContext.get_instance() which owns the singleton.

    In regulated mode: components are initialized eagerly at first access.
    In non-regulated mode: components are initialized lazily on first use.

    Thread-safe initialization using double-checked locking.
    """

    _instance: EnforcementContext | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._identity_bridge: IdentityBridge | None = None
        self._authorization_backend: AuthorizationPolicyBackend | None = None
        self._audit_emitter: Any = None
        self._initialized = False
        self._init_lock = threading.Lock()
        self._regulated = _is_regulated()

    @classmethod
    def get_instance(cls) -> EnforcementContext:
        """Return the process-scoped EnforcementContext singleton."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = cls()
                    if instance._regulated:
                        instance._initialize_eagerly()
                    cls._instance = instance
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance. For testing only."""
        with cls._lock:
            cls._instance = None

    def _initialize_eagerly(self) -> None:
        """Eagerly initialize all components.

        Called automatically when regulated mode is active and instance
        is first created. Can also be called manually to force eager init.
        """
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            if self._authorization_backend is None:
                self._init_authorization_backend()
            if self._identity_bridge is None:
                self._init_identity_bridge()
            if self._audit_emitter is None:
                self._init_audit_emitter()
            self._initialized = True

    def _init_identity_bridge(self) -> None:
        """Initialize the identity bridge."""
        from phlo.identity.bridge import create_regulated_bridge

        self._identity_bridge = create_regulated_bridge(
            canonical_rbac=getattr(self.authorization_backend, "rbac", None)
        )

    def _init_authorization_backend(self) -> None:
        """Initialize the authorization backend."""
        from phlo.capabilities import resolve_capability
        from phlo.capabilities.discovery import discover_capabilities
        from phlo.infrastructure.config import get_configured_authorization_backend_name

        discover_capabilities()
        configured_name = get_configured_authorization_backend_name() or ""

        result = resolve_capability(
            "authorization_policy_backend",
            configured_name or None,
        )
        if result is None:
            msg = (
                "Configured authorization_policy_backend is missing or ambiguous"
                if configured_name
                else "No authorization_policy_backend is configured"
            )
            raise RuntimeError(msg)
        self._authorization_backend = result.provider

    def _init_audit_emitter(self) -> None:
        """Initialize the audit emitter."""
        from phlo.audit.events import create_default_emitter

        self._audit_emitter = create_default_emitter(surface="core")

    @property
    def identity_bridge(self) -> IdentityBridge:
        """Identity bridge (eager in regulated mode, lazy otherwise)."""
        if self._identity_bridge is None:
            with self._init_lock:
                if self._identity_bridge is None:
                    if self._authorization_backend is None:
                        self._init_authorization_backend()
                    self._init_identity_bridge()
        bridge = self._identity_bridge
        if bridge is None:
            raise RuntimeError("Identity bridge initialization did not produce a bridge")
        return bridge

    @property
    def authorization_backend(self) -> AuthorizationPolicyBackend:
        """Authorization backend (eager in regulated mode, lazy otherwise)."""
        if self._authorization_backend is None:
            with self._init_lock:
                if self._authorization_backend is None:
                    self._init_authorization_backend()
        backend = self._authorization_backend
        if backend is None:
            raise RuntimeError("Authorization backend initialization did not produce a backend")
        return backend

    @property
    def audit_emitter(self) -> Any:
        """Audit emitter (eager in regulated mode, lazy otherwise)."""
        if self._audit_emitter is None:
            with self._init_lock:
                if self._audit_emitter is None:
                    self._init_audit_emitter()
        return self._audit_emitter

    def canonicalize(self, auth_principal: Any) -> Principal:
        """Canonicalize only a validated AuthPrincipal via the identity bridge."""
        from phlo.capabilities.interfaces import AuthPrincipal

        if not isinstance(auth_principal, AuthPrincipal):
            raise TypeError("Core enforcement requires an AuthPrincipal")
        return self.identity_bridge.canonicalize(auth_principal)


def enforce(
    principal: Any,
    action: str,
    resource: ResourceRef,
    context: DecisionContext | None = None,
    request_id: str | None = None,
    surface: str = "unknown",
    correlation_id: str | None = None,
    require_durable_audit: bool = False,
) -> EnforcementResult:
    """Make an authorization decision and emit a canonical audit event.

    Core enforcement entrypoint for surface adapters: canonicalizes the
    principal, resolves the PDP, and returns an allow, deny, or error
    EnforcementResult. With ``require_durable_audit``, a permitted operation
    fails closed unless its audit event persists to a durable sink.
    """
    ctx = EnforcementContext.get_instance()

    try:
        canonical_principal = ctx.canonicalize(principal)
    except Exception:
        logger.exception("identity_canonicalization_failed")
        return EnforcementResult.error(
            reason_code="canonicalization_failed",
            explanation="Failed to canonicalize principal",
        )

    try:
        decision: AuthorizationDecision = ctx.authorization_backend.explain_decision(
            canonical_principal, action, resource, context
        )
    except Exception:
        logger.exception("authorization_backend_failed")
        return EnforcementResult.error(
            reason_code="backend_unavailable",
            explanation="Authorization backend failed",
        )

    if decision.allowed:
        result = EnforcementResult.allow()
    else:
        result = EnforcementResult.deny(
            reason_code=decision.reason_code or "explicit_deny",
            policy_id=decision.policy_id,
            explanation=decision.explanation,
        )

    try:
        ctx.audit_emitter.emit_authorization(
            surface=surface,
            action=action,
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            actor_subject=canonical_principal.subject,
            actor_type=canonical_principal.principal_type,
            actor_roles=canonical_principal.roles,
            authentication_source=canonical_principal.attributes.get(
                "authentication_source", "unknown"
            ),
            decision=result.variant,
            reason_code=result.reason_code or "",
            policy_id=result.policy_id,
            request_id=request_id,
            correlation_id=correlation_id,
            require_durable=require_durable_audit,
        )
    except Exception:
        logger.exception("audit_emission_failed")
        if require_durable_audit and result.allowed:
            return EnforcementResult.error(
                reason_code="audit_persistence_failed",
                explanation="Required durable audit persistence failed",
            )

    return result
