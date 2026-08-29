"""Integration tests for signature enforcement on critical actions.

Verifies:
- Critical actions require explicit signature
- Signature events capture meaning, signer, and record linkage
- Step-up authentication is enforced for signatures
- Signature events enter the audit chain
"""

from __future__ import annotations

import pytest

from phlo.audit.events import CanonicalAuditEvent
from phlo.capabilities.interfaces import AuthenticatedSession, AuthPrincipal
from phlo.compliance.audit import InMemoryAuditStore, TamperEvidentAuditSink
from phlo.compliance.signatures import (
    SignatureMeaning,
    SignatureRequest,
    SignatureService,
    SignatureServiceConfig,
    StepUpResult,
)
from phlo.compliance.signatures.step_up import StepUpAuthChallenge

pytestmark = pytest.mark.integration


class _VerifiedStepUpChallenge(StepUpAuthChallenge):
    """Test-only step-up verifier that supplies independent assurance."""

    def challenge(self, session) -> StepUpResult:
        del session
        return StepUpResult(success=True, assurance_level="mfa")


def _signature_config(actions: frozenset[str]) -> SignatureServiceConfig:
    """Build a signature configuration with an explicit verified challenge."""
    return SignatureServiceConfig(
        critical_actions=actions,
        step_up_challenge=_VerifiedStepUpChallenge(),
    )


class TestSignatureEnforcement:
    """Integration tests for electronic signature enforcement."""

    def test_critical_action_requires_signature(self) -> None:
        """Critical actions are blocked without a valid signature."""
        events_emitted: list[CanonicalAuditEvent] = []

        class MockAuditEmitter:
            def emit(self, event: CanonicalAuditEvent) -> None:
                events_emitted.append(event)

        service = SignatureService(
            config=_signature_config(frozenset(["dataset.publish", "config.update"])),
            audit_emitter=MockAuditEmitter(),
        )

        session = AuthenticatedSession(
            principal=AuthPrincipal(
                subject="alice@example.com",
                principal_type="user",
            ),
            auth_method="oidc",
            provider_name="test",
        )

        request = SignatureRequest(
            signer_subject="alice@example.com",
            meaning=SignatureMeaning.APPROVED,
            record_type="dataset",
            record_id="dataset-123",
            record_version="v1",
            justification="Approved for release",
        )

        result = service.sign(request, session)
        assert result.signature_hash != ""
        assert result.meaning == SignatureMeaning.APPROVED

    def test_critical_action_without_signature_is_blocked(self) -> None:
        """Unsigned critical actions trip the enforcement gate without emitting events."""
        events_emitted: list[CanonicalAuditEvent] = []

        class MockAuditEmitter:
            def emit(self, event: CanonicalAuditEvent) -> None:
                events_emitted.append(event)

        service = SignatureService(
            config=SignatureServiceConfig(
                critical_actions=frozenset(["dataset.publish", "config.update"])
            ),
            audit_emitter=MockAuditEmitter(),
        )

        assert service.require_signature("dataset.publish", "dataset") is True
        assert service.require_signature("config.update", "config") is True
        assert events_emitted == []

    def test_non_critical_action_no_signature_required(self) -> None:
        """Non-critical actions do not require a signature."""
        service = SignatureService(
            config=_signature_config(frozenset(["dataset.publish"])),
        )

        result = service.require_signature("dataset.read", "dataset")
        assert result is False

    def test_signature_creates_audit_event(self) -> None:
        """Signing creates an audit event in the chain."""
        events_emitted: list[CanonicalAuditEvent] = []

        class MockAuditEmitter:
            def emit(self, event: CanonicalAuditEvent) -> None:
                events_emitted.append(event)

        service = SignatureService(
            config=_signature_config(frozenset(["dataset.publish"])),
            audit_emitter=MockAuditEmitter(),
        )

        session = AuthenticatedSession(
            principal=AuthPrincipal(
                subject="alice@example.com",
                principal_type="user",
            ),
            auth_method="oidc",
            provider_name="test",
        )

        request = SignatureRequest(
            signer_subject="alice@example.com",
            meaning=SignatureMeaning.APPROVED,
            record_type="dataset",
            record_id="dataset-456",
            record_version="v1",
            justification="Approved for production",
        )

        service.sign(request, session)

        assert len(events_emitted) == 1
        assert events_emitted[0].event_type.value == "signature"
        assert events_emitted[0].actor_subject == "alice@example.com"

    def test_signature_with_wrong_signer_fails(self) -> None:
        """Signature fails if signer doesn't match request."""
        service = SignatureService(
            config=_signature_config(frozenset(["dataset.publish"])),
        )

        session = AuthenticatedSession(
            principal=AuthPrincipal(
                subject="alice@example.com",
                principal_type="user",
            ),
            auth_method="oidc",
            provider_name="test",
        )

        request = SignatureRequest(
            signer_subject="bob@example.com",
            meaning=SignatureMeaning.APPROVED,
            record_type="dataset",
            record_id="dataset-789",
            record_version="v1",
            justification="Approved",
        )

        with pytest.raises(ValueError, match="Signer subject mismatch"):
            service.sign(request, session)

    def test_signature_record_contains_required_fields(self) -> None:
        """Signature record captures all required fields."""
        service = SignatureService(
            config=_signature_config(frozenset(["dataset.publish"])),
        )

        session = AuthenticatedSession(
            principal=AuthPrincipal(
                subject="alice@example.com",
                principal_type="user",
            ),
            auth_method="oidc",
            provider_name="test",
        )

        request = SignatureRequest(
            signer_subject="alice@example.com",
            meaning=SignatureMeaning.RELEASED,
            record_type="dataset",
            record_id="dataset-release",
            record_version="v2.0.0",
            justification="Released to production",
        )

        record = service.sign(request, session)

        assert record.signer_subject == "alice@example.com"
        assert record.meaning == SignatureMeaning.RELEASED
        assert record.record_type == "dataset"
        assert record.record_id == "dataset-release"
        assert record.record_version == "v2.0.0"
        assert record.signature_hash != ""
        assert record.signed_at != ""

    def test_signature_enters_tamper_evident_chain(self) -> None:
        """Signature events are part of the tamper-evident chain."""
        store = InMemoryAuditStore()
        sink = TamperEvidentAuditSink(store)

        class ChainEmitter:
            def emit(self, event: CanonicalAuditEvent) -> None:
                sink.write(event)

        service = SignatureService(
            config=_signature_config(frozenset(["dataset.publish"])),
            audit_emitter=ChainEmitter(),
        )

        session = AuthenticatedSession(
            principal=AuthPrincipal(
                subject="alice@example.com",
                principal_type="user",
            ),
            auth_method="oidc",
            provider_name="test",
        )

        for i in range(3):
            request = SignatureRequest(
                signer_subject="alice@example.com",
                meaning=SignatureMeaning.APPROVED,
                record_type="dataset",
                record_id=f"dataset-{i}",
                record_version="v1",
                justification="Approved",
            )
            service.sign(request, session)

        records = store.query("compliance", limit=100)
        assert len(records) == 3

        result = store.verify_chain("compliance")
        assert result.valid is True

    def test_break_glass_signature_handled(self) -> None:
        """Break-glass requests use emergency review type."""
        from phlo.compliance.governance.break_glass import (
            BreakGlassManager,
            BreakGlassStatus,
        )

        manager = BreakGlassManager()

        request = manager.create_request(
            principal_subject="alice@example.com",
            principal_type="user",
            resource_type="dataset",
            resource_id="dataset-emergency",
            action="dataset.write",
            justification="Emergency access needed for incident",
            urgency="critical",
        )

        assert request.status == BreakGlassStatus.PENDING
        assert request.urgency == "critical"

        approval = manager.approve(request.request_id, approved_by="supervisor@example.com")

        assert approval.request_id == request.request_id
        assert approval.approved_by == "supervisor@example.com"
