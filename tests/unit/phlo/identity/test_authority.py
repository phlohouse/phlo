"""Behavioral tests for Phlo's durable identity authority contract."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from phlo.capabilities.interfaces import AuthPrincipal
from phlo.compliance.signatures.types import SignatureMeaning, SignatureRecord, SignatureRequest
from phlo.identity.authority import IdentityAuthority, IdentityConflictError
from phlo.identity.bridge import IdentityBridge
from phlo.plugins.observatory_settings import InMemorySettingsService, SettingsScope


def _authority() -> IdentityAuthority:
    return IdentityAuthority(InMemorySettingsService())


def test_invitation_claim_is_email_bound_and_single_use() -> None:
    authority = _authority()
    invitation, token = authority.create_invitation(
        email="alice@example.com", roles=["analyst"], invited_by="bootstrap-admin"
    )

    with pytest.raises(IdentityConflictError):
        authority.accept_invitation(token=token, subject="alice-id", email="mallory@example.com")

    member = authority.accept_invitation(token=token, subject="alice-id", email="ALICE@example.com")

    assert invitation.status == "pending"
    assert member.roles == ("analyst",)
    assert authority.managed_roles("alice-id", "user") == ("analyst",)
    assert authority.invitations()[0].status == "accepted"
    with pytest.raises(IdentityConflictError):
        authority.accept_invitation(token=token, subject="another-id", email="alice@example.com")


def test_expired_invitation_cannot_be_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    authority = _authority()
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr("phlo.identity.authority._now", lambda: created_at)
    _, token = authority.create_invitation(
        email="alice@example.com",
        roles=["viewer"],
        invited_by="bootstrap-admin",
        ttl=timedelta(seconds=1),
    )
    monkeypatch.setattr("phlo.identity.authority._now", lambda: created_at + timedelta(seconds=2))
    with pytest.raises(IdentityConflictError, match="expired"):
        authority.accept_invitation(token=token, subject="alice-id", email="alice@example.com")


def test_role_updates_require_current_version_and_override_group_roles() -> None:
    authority = _authority()
    _, token = authority.create_invitation(
        email="alice@example.com", roles=["admin"], invited_by="bootstrap-admin"
    )
    member = authority.accept_invitation(token=token, subject="alice-id", email="alice@example.com")

    updated = authority.assign_roles(
        subject=member.subject,
        email=member.email,
        principal_type="user",
        roles=["viewer"],
        expected_version=member.version,
    )

    assert updated.version == 2
    assert authority.managed_roles("alice-id", "user") == ("viewer",)
    with pytest.raises(IdentityConflictError):
        authority.assign_roles(
            subject=member.subject,
            email=member.email,
            principal_type="user",
            roles=["admin"],
            expected_version=member.version,
        )


def test_service_account_secret_is_one_time_and_revocable() -> None:
    authority = _authority()
    account, token = authority.create_service_account(name="scheduler", roles=["service"])

    assert authority.authenticate_service_token(token) == (account.subject, "scheduler")
    assert all("token" not in item.__dict__ for item in authority.service_accounts())

    revoked = authority.revoke_service_account(account.subject, expected_version=account.version)

    assert revoked.active is False
    assert authority.authenticate_service_token(token) is None


def test_service_account_creation_key_prevents_duplicate_secret_issuance() -> None:
    authority = _authority()
    account, token = authority.create_service_account(
        name="scheduler",
        roles=["service"],
        idempotency_key="create-scheduler-1",
        actor_subject="admin-id",
    )

    with pytest.raises(IdentityConflictError, match="one-time token is not replayed"):
        authority.create_service_account(
            name="scheduler",
            roles=["service"],
            idempotency_key="create-scheduler-1",
            actor_subject="admin-id",
        )

    assert len(authority.service_accounts()) == 1
    assert authority.authenticate_service_token(token) == (account.subject, "scheduler")


def test_invalid_roles_and_duplicate_pending_invitation_are_rejected() -> None:
    authority = _authority()
    with pytest.raises(ValueError, match="role names"):
        authority.create_invitation(
            email="alice@example.com", roles=["superuser"], invited_by="admin"
        )
    authority.create_invitation(email="alice@example.com", roles=["viewer"], invited_by="admin")
    with pytest.raises(IdentityConflictError, match="already exists"):
        authority.create_invitation(
            email="ALICE@example.com", roles=["analyst"], invited_by="admin"
        )


def test_managed_roles_override_stale_identity_provider_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHLO_IDENTITY_AUTHORITY_ENABLED", "1")

    class ManagedRoles:
        def managed_roles(self, subject: str, principal_type: str) -> tuple[str, ...]:
            assert subject == "alice-id"
            assert principal_type == "user"
            return ("viewer",)

    monkeypatch.setattr("phlo.identity.authority.IdentityAuthority", lambda: ManagedRoles())
    principal = IdentityBridge().canonicalize(
        AuthPrincipal(subject="alice-id", principal_type="user", groups=("admin",))
    )

    assert principal.roles == ("viewer",)


def test_signatures_are_actor_action_target_version_bound_and_single_use() -> None:
    authority = _authority()
    expected = SignatureRequest(
        signer_subject="admin-id",
        meaning=SignatureMeaning.APPROVED,
        action="admin.role.change",
        record_type="member",
        record_id="alice-id",
        record_version="2",
    )
    record = SignatureRecord.from_request(expected, authentication_assurance="mfa")
    authority.save_signature(record)

    assert (
        authority.consume_signature(record.signature_id, replace(expected, action="admin.invite"))
        is False
    )
    assert (
        authority.consume_signature(record.signature_id, replace(expected, record_id="bob-id"))
        is False
    )
    assert (
        authority.consume_signature(record.signature_id, replace(expected, record_version="3"))
        is False
    )
    assert (
        authority.consume_signature(record.signature_id, replace(expected, signer_subject="other"))
        is False
    )
    assert authority.consume_signature(record.signature_id, expected) is True
    assert authority.consume_signature(record.signature_id, expected) is False
    assert authority.signatures("admin-id")[0].consumed_at is not None


def test_signature_without_step_up_assurance_cannot_authorize_action() -> None:
    authority = _authority()
    expected = SignatureRequest(
        signer_subject="admin-id",
        meaning=SignatureMeaning.APPROVED,
        action="admin.role.change",
        record_type="member",
        record_id="alice-id",
        record_version="2",
    )
    record = SignatureRecord.from_request(expected, authentication_assurance="session")
    authority.save_signature(record)

    assert authority.consume_signature(record.signature_id, expected) is False


def test_tampered_signature_record_fails_verification() -> None:
    store = InMemorySettingsService()
    authority = IdentityAuthority(store)
    expected = SignatureRequest(
        signer_subject="admin-id",
        meaning=SignatureMeaning.APPROVED,
        action="admin.role.change",
        record_type="member",
        record_id="alice-id",
        record_version="2",
    )
    record = SignatureRecord.from_request(expected, authentication_assurance="mfa")
    authority.save_signature(record)

    def tamper(current):
        state = current
        state["signatures"][record.signature_id]["action"] = "admin.invite"
        return state

    store.mutate(
        SettingsScope.GLOBAL,
        "phlo.identity.authority",
        tamper,
    )

    assert authority.consume_signature(record.signature_id, expected) is False
