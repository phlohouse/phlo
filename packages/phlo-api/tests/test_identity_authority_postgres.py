"""Disposable-PostgreSQL tests for cross-process identity guarantees."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from testcontainers.postgres import PostgresContainer

from phlo.compliance.signatures.types import SignatureMeaning, SignatureRecord, SignatureRequest
from phlo.identity.authority import IdentityAuthority, IdentityConflictError
from phlo_postgres.settings_store import PostgresSettingsStore
from phlo.capabilities.authorization import DefaultAuthorizationPolicyBackend
from phlo.cli.commands import authz_identity
from phlo.security.enforcement import EnforcementContext
from click.testing import CliRunner
from test_v1_admin_identity import _client
from phlo_api.api import v1_admin_identity


@pytest.mark.integration
def test_shared_authorities_serialize_invitation_acceptance_and_signature_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Separate store instances share state and atomically consume one-time rights."""
    # Disposable databases must not inherit missing or empty deployment credentials.
    with PostgresContainer("postgres:18-alpine", password="test") as postgres:
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


@pytest.mark.integration
def test_cli_signed_assignment_changes_real_postgres_effective_access(monkeypatch) -> None:
    """Public CLI/HTTP and real enforcement/storage; test session, not browser login proof."""
    with PostgresContainer("postgres:18-alpine", password="test") as postgres:
        monkeypatch.setenv(
            "PHLO_OBSERVATORY_SETTINGS_DB_URL", postgres.get_connection_url(driver=None)
        )
        authority = IdentityAuthority(PostgresSettingsStore())
        authority.assign_roles(
            subject="admin-id",
            email="admin@example.com",
            principal_type="user",
            roles=["admin", "viewer"],
            expected_version=0,
        )
        client = _client(monkeypatch, authority)
        backend = DefaultAuthorizationPolicyBackend(
            policies=[
                {
                    "policy_id": "analyst-query",
                    "effect": "allow",
                    "action": "dataset.query",
                    "principal": {"roles": ["analyst"]},
                    "resource": {"type": "dataset", "id_pattern": "sales.*"},
                },
                {
                    "policy_id": "restricted-query",
                    "effect": "deny",
                    "action": "dataset.query",
                    "principal": {"roles": ["analyst"]},
                    "resource": {"type": "dataset", "id_pattern": "sales.restricted"},
                },
            ]
        )
        monkeypatch.setattr(v1_admin_identity, "get_authorization_backend", lambda: backend)
        context = EnforcementContext()
        monkeypatch.setattr(context, "_authorization_backend", backend)
        monkeypatch.setattr(EnforcementContext, "get_instance", lambda: context)
        monkeypatch.setattr("phlo.identity.authority.IdentityAuthority", lambda: authority)
        monkeypatch.setattr(authz_identity, "_client", lambda _ctx: client)
        params = {
            "action": "dataset.query",
            "resource_type": "dataset",
            "resource_id": "sales.orders",
        }
        assert client.get("/api/v1/admin/permissions", params=params).json()["allowed"] is False
        args = [
            "--api-url",
            "https://api.example",
            "assign",
            "admin-id",
            "--role",
            "admin",
            "--role",
            "analyst",
            "--expected-version",
            "1",
            "--justification",
            "Reviewed query access",
            "--operation-id",
            "postgres-role-change",
            "--yes",
        ]
        result = CliRunner().invoke(
            authz_identity.identity_group, args, env={"PHLO_AUTHZ_TOKEN": "test-session"}
        )
        assert result.exit_code == 0, result.output
        assert IdentityAuthority(PostgresSettingsStore()).member("admin-id").roles == (
            "admin",
            "analyst",
        )
        assert client.get("/api/v1/admin/permissions", params=params).json()["allowed"] is True
        denied = client.get(
            "/api/v1/admin/permissions", params={**params, "resource_id": "sales.restricted"}
        )
        assert denied.json() == {"allowed": False, "reason_code": "explicit_deny"}
        outside = client.get(
            "/api/v1/admin/permissions", params={**params, "resource_id": "finance.orders"}
        )
        assert outside.json()["allowed"] is False
        # Standalone router lacks operation middleware: consumed signatures reject a replay.
        replay = CliRunner().invoke(
            authz_identity.identity_group, args, env={"PHLO_AUTHZ_TOKEN": "test-session"}
        )
        assert replay.exit_code != 0
        assert "409" in replay.output
        assert IdentityAuthority(PostgresSettingsStore()).member("admin-id").version == 2
