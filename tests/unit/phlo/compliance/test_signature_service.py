"""Tests for the signature service.

Covers default and custom critical-action configuration, signature request
construction, record creation, and step-up authentication challenges.
"""

from __future__ import annotations

import pytest

from phlo.capabilities.interfaces import AuthenticatedSession, AuthPrincipal
from phlo.compliance.signatures import (
    SignatureMeaning,
    SignatureRecord,
    SignatureRequest,
    SignatureService,
    SignatureServiceConfig,
    StepUpResult,
)
from phlo.compliance.signatures.step_up import SessionConfirmChallenge, StepUpAuthChallenge


class TestSignatureServiceConfig:
    """Tests for SignatureServiceConfig."""

    def test_default_critical_actions(self) -> None:
        """Default config has expected critical actions."""
        config = SignatureServiceConfig()
        assert "dataset.publish" in config.critical_actions
        assert "asset.approve" in config.critical_actions
        assert "admin.manage" in config.critical_actions

    def test_custom_critical_actions(self) -> None:
        """Custom critical actions can be specified."""
        custom_actions = frozenset(["custom.action"])
        config = SignatureServiceConfig(critical_actions=custom_actions)
        assert config.critical_actions == custom_actions


class TestSignatureRequest:
    """Tests for SignatureRequest."""

    def test_create_signature_request(self) -> None:
        """SignatureRequest can be created with required fields."""
        request = SignatureRequest(
            signer_subject="alice@example.com",
            meaning=SignatureMeaning.APPROVED,
            record_type="dataset",
            record_id="dataset-123",
            record_version="abc123",
        )
        assert request.signer_subject == "alice@example.com"
        assert request.meaning == SignatureMeaning.APPROVED
        assert request.record_type == "dataset"
        assert request.record_id == "dataset-123"
        assert request.record_version == "abc123"
        assert request.justification is None

    def test_create_signature_request_with_justification(self) -> None:
        """SignatureRequest can include optional justification."""
        request = SignatureRequest(
            signer_subject="alice@example.com",
            meaning=SignatureMeaning.RELEASED,
            record_type="dataset",
            record_id="dataset-123",
            record_version="abc123",
            justification="Approved for production use",
        )
        assert request.justification == "Approved for production use"


class TestSignatureRecord:
    """Tests for SignatureRecord."""

    def test_from_request_creates_record(self) -> None:
        """SignatureRecord can be created from a SignatureRequest."""
        request = SignatureRequest(
            signer_subject="alice@example.com",
            meaning=SignatureMeaning.APPROVED,
            record_type="dataset",
            record_id="dataset-123",
            record_version="abc123",
        )

        record = SignatureRecord.from_request(request)

        assert record.signer_subject == "alice@example.com"
        assert record.meaning == SignatureMeaning.APPROVED
        assert record.record_type == "dataset"
        assert record.record_id == "dataset-123"
        assert record.record_version == "abc123"
        assert record.signature_id is not None
        assert record.signed_at is not None
        assert record.authentication_assurance == "session"
        assert record.signature_hash != ""

    def test_from_request_requires_explicit_hmac_key_in_regulated_mode(self, monkeypatch) -> None:
        """Regulated signatures must not fall back to the deterministic dev key."""
        monkeypatch.setenv("PHLO_REGULATED", "true")
        monkeypatch.delenv("PHLO_SIGNATURE_HMAC_KEY", raising=False)
        monkeypatch.delenv("PHLO_AUDIT_HMAC_KEY", raising=False)
        request = SignatureRequest(
            signer_subject="alice@example.com",
            meaning=SignatureMeaning.APPROVED,
            record_type="dataset",
            record_id="dataset-123",
            record_version="abc123",
        )

        with pytest.raises(RuntimeError, match="PHLO_SIGNATURE_HMAC_KEY or PHLO_AUDIT_HMAC_KEY"):
            SignatureRecord.from_request(request)

    def test_from_request_can_use_audit_hmac_key_in_regulated_mode(self, monkeypatch) -> None:
        """Regulated signatures may share configured audit HMAC material."""
        monkeypatch.setenv("PHLO_REGULATED", "true")
        monkeypatch.delenv("PHLO_SIGNATURE_HMAC_KEY", raising=False)
        monkeypatch.setenv("PHLO_AUDIT_HMAC_KEY", "test-audit-key")
        request = SignatureRequest(
            signer_subject="alice@example.com",
            meaning=SignatureMeaning.APPROVED,
            record_type="dataset",
            record_id="dataset-123",
            record_version="abc123",
        )

        record = SignatureRecord.from_request(request)

        assert record.signature_hash != ""

    def test_from_request_with_custom_assurance(self) -> None:
        """SignatureRecord can be created with custom authentication assurance."""
        request = SignatureRequest(
            signer_subject="alice@example.com",
            meaning=SignatureMeaning.APPROVED,
            record_type="dataset",
            record_id="dataset-123",
            record_version="abc123",
        )

        record = SignatureRecord.from_request(request, authentication_assurance="mfa")

        assert record.authentication_assurance == "mfa"

    def test_different_records_produce_different_hashes(self) -> None:
        """Different records produce different signature hashes."""
        request1 = SignatureRequest(
            signer_subject="alice@example.com",
            meaning=SignatureMeaning.APPROVED,
            record_type="dataset",
            record_id="dataset-123",
            record_version="abc123",
        )
        request2 = SignatureRequest(
            signer_subject="bob@example.com",
            meaning=SignatureMeaning.APPROVED,
            record_type="dataset",
            record_id="dataset-123",
            record_version="abc123",
        )

        record1 = SignatureRecord.from_request(request1)
        record2 = SignatureRecord.from_request(request2)

        assert record1.signature_hash != record2.signature_hash


class MockStepUpChallenge(StepUpAuthChallenge):
    """Mock step-up challenge for testing."""

    def __init__(self, success: bool = True, assurance: str = "mfa") -> None:
        self._success = success
        self._assurance = assurance

    def challenge(self, session: AuthenticatedSession) -> StepUpResult:
        return StepUpResult(success=self._success, assurance_level=self._assurance)


class TestSignatureService:
    """Tests for SignatureService."""

    def _make_session(self, subject: str = "alice@example.com") -> AuthenticatedSession:
        """Create a test session."""
        return AuthenticatedSession(
            principal=AuthPrincipal(subject=subject, principal_type="user"),
            auth_method="oidc",
            provider_name="test",
        )

    def test_require_signature_for_critical_action(self) -> None:
        """Critical actions require signatures."""
        service = SignatureService()
        assert service.require_signature("dataset.publish", "dataset") is True
        assert service.require_signature("asset.approve", "asset") is True

    def test_require_signature_for_non_critical_action(self) -> None:
        """Non-critical actions do not require signatures."""
        service = SignatureService()
        assert service.require_signature("dataset.read", "dataset") is False
        assert service.require_signature("dataset.write", "dataset") is False

    def test_sign_creates_signature_record(self) -> None:
        """sign() creates a SignatureRecord with audit event."""
        service = SignatureService(
            config=SignatureServiceConfig(critical_actions=frozenset(["dataset.publish"])),
            audit_emitter=None,
        )
        session = self._make_session()
        request = SignatureRequest(
            signer_subject="alice@example.com",
            meaning=SignatureMeaning.APPROVED,
            record_type="dataset",
            record_id="dataset-123",
            record_version="abc123",
        )

        record = service.sign(request, session)

        assert record.signer_subject == "alice@example.com"
        assert record.meaning == SignatureMeaning.APPROVED
        assert record.signature_hash != ""

    def test_sign_fails_on_signer_mismatch(self) -> None:
        """sign() fails when signer does not match session principal."""
        service = SignatureService()
        session = self._make_session(subject="alice@example.com")
        request = SignatureRequest(
            signer_subject="bob@example.com",
            meaning=SignatureMeaning.APPROVED,
            record_type="dataset",
            record_id="dataset-123",
            record_version="abc123",
        )

        with pytest.raises(ValueError, match="Signer subject mismatch"):
            service.sign(request, session)

    def test_sign_fails_on_step_up_failure(self) -> None:
        """sign() fails when step-up authentication fails."""
        service = SignatureService(
            config=SignatureServiceConfig(
                critical_actions=frozenset(["dataset.publish"]),
                step_up_challenge=MockStepUpChallenge(success=False),
            ),
        )
        session = self._make_session()
        request = SignatureRequest(
            signer_subject="alice@example.com",
            meaning=SignatureMeaning.APPROVED,
            record_type="dataset",
            record_id="dataset-123",
            record_version="abc123",
        )

        with pytest.raises(PermissionError, match="Step-up authentication failed"):
            service.sign(request, session)

    def test_sign_records_assurance_level(self) -> None:
        """sign() records the assurance level from step-up challenge."""
        service = SignatureService(
            config=SignatureServiceConfig(
                critical_actions=frozenset(["dataset.publish"]),
                step_up_challenge=MockStepUpChallenge(success=True, assurance="re-authenticated"),
            ),
        )
        session = self._make_session()
        request = SignatureRequest(
            signer_subject="alice@example.com",
            meaning=SignatureMeaning.APPROVED,
            record_type="dataset",
            record_id="dataset-123",
            record_version="abc123",
        )

        record = service.sign(request, session)

        assert record.authentication_assurance == "re-authenticated"


class TestSessionConfirmChallenge:
    """Tests for SessionConfirmChallenge."""

    def test_challenge_always_succeeds(self) -> None:
        """SessionConfirmChallenge always returns success."""
        session = AuthenticatedSession(
            principal=AuthPrincipal(subject="alice@example.com", principal_type="user"),
            auth_method="oidc",
            provider_name="test",
        )

        challenge = SessionConfirmChallenge()
        result = challenge.challenge(session)

        assert result.success is True
        assert result.assurance_level == "session"
        assert result.message is not None


class TestSignatureMeaning:
    """Tests for SignatureMeaning enum."""

    def test_all_meanings_exist(self) -> None:
        """All expected signature meanings exist."""
        assert SignatureMeaning.APPROVED == "approved"
        assert SignatureMeaning.RELEASED == "released"
        assert SignatureMeaning.REVIEWED == "reviewed"
        assert SignatureMeaning.ACKNOWLEDGED == "acknowledged"
        assert SignatureMeaning.AUTHORED == "authored"

    def test_meanings_are_strings(self) -> None:
        """Signature meanings are string values."""
        for meaning in SignatureMeaning:
            assert isinstance(meaning, str)
