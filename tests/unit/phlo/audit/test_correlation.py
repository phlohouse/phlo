"""Tests for audit correlation ID propagation.

Covers CanonicalAuditEvent correlation fields and their defaults, the
enforce() path forwarding (or omitting) the caller's correlation ID to
the audit emitter, and EnforcementContext lifecycle: lazy component
initialization stays incomplete until eager init, and a failed eager
init must not publish a broken singleton.
"""

from __future__ import annotations

from phlo.audit.events import CanonicalAuditEvent


class TestCorrelationFields:
    def test_default_empty_correlation_id(self):
        event = CanonicalAuditEvent(event_type="authorization", surface="test")
        assert event.correlation_id == ""
        assert event.parent_correlation_id == ""

    def test_set_correlation_id(self):
        event = CanonicalAuditEvent(
            event_type="authorization",
            surface="test",
            correlation_id="req-123",
        )
        assert event.correlation_id == "req-123"

    def test_set_parent_correlation_id(self):
        event = CanonicalAuditEvent(
            event_type="authorization",
            surface="test",
            correlation_id="req-456",
            parent_correlation_id="req-123",
        )
        assert event.correlation_id == "req-456"
        assert event.parent_correlation_id == "req-123"

    def test_from_authorization_decision_with_correlation(self):
        event = CanonicalAuditEvent.from_authorization_decision(
            surface="phlo-api",
            actor_subject="alice@example.com",
            actor_type="user",
            actor_roles=("viewer",),
            authentication_source="proxy",
            action="dataset.read",
            resource_type="dataset",
            resource_id="orders",
            decision="allow",
            reason_code="",
            correlation_id="corr-789",
        )
        assert event.correlation_id == "corr-789"

    def test_from_authorization_decision_default_correlation(self):
        event = CanonicalAuditEvent.from_authorization_decision(
            surface="phlo-api",
            actor_subject="alice@example.com",
            actor_type="user",
            actor_roles=(),
            authentication_source="proxy",
            action="dataset.read",
            resource_type="dataset",
            resource_id="orders",
            decision="allow",
            reason_code="",
        )
        assert event.correlation_id == ""


class TestEnforceCorrelation:
    def test_enforce_passes_correlation_to_audit(self):
        from phlo.capabilities.interfaces import AuthPrincipal, ResourceRef
        from phlo.security.enforcement import enforce
        from tests.helpers import stubbed_enforcement_context

        with stubbed_enforcement_context() as stubs:
            enforce(
                principal=AuthPrincipal(
                    subject="alice@example.com",
                    principal_type="user",
                    groups=(),
                    attributes={"authentication_source": "proxy"},
                ),
                action="dataset.read",
                resource=ResourceRef(resource_type="dataset", resource_id="orders"),
                surface="phlo-api",
                correlation_id="corr-abc-123",
            )

            call_kwargs = stubs.emitter.emit_authorization.call_args.kwargs
            assert call_kwargs["correlation_id"] == "corr-abc-123"

    def test_enforce_without_correlation_passes_none(self):
        from phlo.capabilities.interfaces import AuthPrincipal, ResourceRef
        from phlo.security.enforcement import enforce
        from tests.helpers import stubbed_enforcement_context

        with stubbed_enforcement_context() as stubs:
            enforce(
                principal=AuthPrincipal(
                    subject="alice@example.com",
                    principal_type="user",
                    groups=(),
                    attributes={"authentication_source": "proxy"},
                ),
                action="dataset.read",
                resource=ResourceRef(resource_type="dataset", resource_id="orders"),
                surface="phlo-api",
            )

            call_kwargs = stubs.emitter.emit_authorization.call_args.kwargs
            assert call_kwargs["correlation_id"] is None


class TestEnforcementContextInitialization:
    def test_lazy_property_access_does_not_mark_context_fully_initialized(self, monkeypatch):
        from phlo.security.enforcement import EnforcementContext

        ctx = EnforcementContext()
        calls: list[str] = []

        def init_identity_bridge() -> None:
            calls.append("bridge")
            ctx._identity_bridge = object()

        def init_authorization_backend() -> None:
            calls.append("backend")
            ctx._authorization_backend = object()

        def init_audit_emitter() -> None:
            calls.append("emitter")
            ctx._audit_emitter = object()

        monkeypatch.setattr(ctx, "_init_identity_bridge", init_identity_bridge)
        monkeypatch.setattr(ctx, "_init_authorization_backend", init_authorization_backend)
        monkeypatch.setattr(ctx, "_init_audit_emitter", init_audit_emitter)

        _ = ctx.identity_bridge

        assert calls == ["backend", "bridge"]
        assert ctx._initialized is False

        ctx._initialize_eagerly()

        assert calls == ["backend", "bridge", "emitter"]
        assert ctx._initialized is True

    def test_get_instance_does_not_publish_failed_eager_init(self, monkeypatch):
        from phlo.security.enforcement import EnforcementContext

        EnforcementContext.reset_instance()
        monkeypatch.setattr("phlo.security.enforcement._is_regulated", lambda: True)

        failures = {"count": 0}

        def failing_init(self) -> None:
            failures["count"] += 1
            raise RuntimeError("backend missing")

        monkeypatch.setattr(EnforcementContext, "_initialize_eagerly", failing_init)

        for _ in range(2):
            try:
                EnforcementContext.get_instance()
            except RuntimeError as exc:
                assert str(exc) == "backend missing"
            else:
                raise AssertionError("Expected eager initialization failure")

        assert failures["count"] == 2
        assert EnforcementContext._instance is None
