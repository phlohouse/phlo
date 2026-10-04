"""Disposable-PostgreSQL tests for cross-process identity guarantees."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from testcontainers.postgres import PostgresContainer

from phlo.compliance.signatures.types import SignatureMeaning, SignatureRecord, SignatureRequest
from phlo.identity.authority import IdentityAuthority, IdentityConflictError
from phlo_postgres.settings_store import PostgresSettingsStore


@pytest.mark.integration
def test_shared_authorities_serialize_invitation_acceptance_and_signature_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Separate store instances share state and atomically consume one-time rights."""
    with PostgresContainer("postgres:18-alpine") as postgres:
        monkeypatch.setenv(
            "PHLO_OBSERVATORY_SETTINGS_DB_URL", postgres.get_connection_url(driver=None)
        )
        authorities = [IdentityAuthority(PostgresSettingsStore()) for _ in range(2)]
        created, token = authorities[0].create_invitation(
            email="alice@example.com", roles=["analyst"], invited_by="admin-id"
        )

        def accept(authority: IdentityAuthority):
            try:
                return authority.accept_invitation(
                    token=token,
                    subject="alice-id",
                    email="alice@example.com",
                )
            except IdentityConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as workers:
            accepted = list(workers.map(accept, authorities))
        assert sum(member is not None for member in accepted) == 1
        assert authorities[1].invitations()[0].invitation_id == created.invitation_id
        assert authorities[1].members()[0].subject == "alice-id"

        request = SignatureRequest(
            signer_subject="admin-id",
            meaning=SignatureMeaning.APPROVED,
            action="admin.member.roles.change",
            record_type="member",
            record_id="alice-id",
            record_version="1:roles-digest",
        )
        signature = SignatureRecord.from_request(request, authentication_assurance="mfa")
        authorities[0].save_signature(signature)

        def consume(authority: IdentityAuthority) -> bool:
            return authority.consume_signature(signature.signature_id, request)

        with ThreadPoolExecutor(max_workers=2) as workers:
            consumed = list(workers.map(consume, authorities))
        assert sorted(consumed) == [False, True]
        assert authorities[1].signatures("admin-id")[0].consumed_at is not None

        def create_service_account(authority: IdentityAuthority):
            try:
                return authority.create_service_account(
                    name="scheduler",
                    roles=["service"],
                    idempotency_key="create-scheduler-1",
                    actor_subject="admin-id",
                )
            except IdentityConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as workers:
            accounts = list(workers.map(create_service_account, authorities))
        assert sum(account is not None for account in accounts) == 1
        assert len(authorities[0].service_accounts()) == 1
