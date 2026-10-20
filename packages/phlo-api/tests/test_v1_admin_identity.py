"""API tests for Phlo-owned identity management and step-up signatures."""

from __future__ import annotations

import time

import pytest
from click.testing import CliRunner
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from phlo.capabilities.interfaces import AuthPrincipal, AuthResult, AuthenticatedSession
from phlo.compliance.signatures.types import SignatureMeaning, SignatureRecord, SignatureRequest
from phlo.identity.authority import IdentityAuthority
from phlo.capabilities.authorization import DefaultAuthorizationPolicyBackend
from phlo.cli.commands import authz_identity
from phlo.plugins.observatory_settings import InMemorySettingsService, StorageUnavailableError
from phlo_api.api import v1_admin_identity
from phlo_api.errors import PhloApiError, error_envelope


def _client(monkeypatch, authority: IdentityAuthority) -> TestClient:
    actor = AuthPrincipal(
        subject="admin-id",
        principal_type="user",
        issuer="test-idp",
        email="admin@example.com",
        groups=("admin",),
        claims={"auth_time": time.time(), "amr": ["pwd", "mfa"]},
    )
    session = AuthenticatedSession(
        principal=actor,
        auth_method="bearer_token",
        provider_name="jwt",
        attributes={
            "jwt_issuer": "https://issuer.example",
            "jwt_audience": "phlo-api",
            "jwt_issuer_validated": "true",
            "jwt_audience_validated": "true",
        },
    )
    monkeypatch.setenv("PHLO_IDENTITY_AUTHORITY_ENABLED", "1")
    monkeypatch.setattr(v1_admin_identity, "_authority", lambda: authority)
    monkeypatch.setattr(v1_admin_identity, "_audit", lambda _event: None)
    monkeypatch.setattr(v1_admin_identity, "get_request_principal", lambda _request: actor)
    monkeypatch.setattr(
        v1_admin_identity,
        "authenticate_request",
        lambda _request: AuthResult(authenticated=True, principal=actor, session=session),
    )
    app = FastAPI()
    app.include_router(v1_admin_identity.router, prefix="/api/v1")

    @app.exception_handler(PhloApiError)
    async def handle_api_error(_request: Request, exc: PhloApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=error_envelope(exc))

    return TestClient(app)


def _sign(
    authority: IdentityAuthority,
    *,
    action: str,
    target_type: str,
    target_id: str,
    target_version: str,
) -> SignatureRecord:
    record = SignatureRecord.from_request(
        SignatureRequest(
            signer_subject="admin-id",
            meaning=SignatureMeaning.APPROVED,
            action=action,
            record_type=target_type,
            record_id=target_id,
            record_version=target_version,
            justification="Approved after review",
        ),
        authentication_assurance="mfa",
    )
    authority.save_signature(record)
    return record


def test_role_change_requires_signature_for_exact_payload_and_is_single_use(monkeypatch) -> None:
    authority = IdentityAuthority(InMemorySettingsService())
    client = _client(monkeypatch, authority)
    signed_version = v1_admin_identity._payload_version(
        0,
        {
            "roles": ["viewer"],
            "active": True,
            "email": "alice@example.com",
            "principal_type": "user",
        },
    )
    signature = _sign(
        authority,
        action="admin.member.roles.change",
        target_type="member",
        target_id="alice-id",
        target_version=signed_version,
    )
    body = {
        "expected_version": 0,
        "roles": ["admin"],
        "email": "alice@example.com",
        "principal_type": "user",
        "active": True,
        "signature_id": signature.signature_id,
    }

    rejected = client.patch("/api/v1/admin/members/alice-id/roles", json=body)
    assert rejected.status_code == 409
    assert authority.member("alice-id") is None

    body["roles"] = ["viewer"]
    accepted = client.patch("/api/v1/admin/members/alice-id/roles", json=body)
    assert accepted.status_code == 200
    assert accepted.json()["roles"] == ["viewer"]
    assert accepted.json()["version"] == 1
    assert client.patch("/api/v1/admin/members/alice-id/roles", json=body).status_code == 409


def test_signature_api_requires_recent_verified_mfa(monkeypatch) -> None:
    authority = IdentityAuthority(InMemorySettingsService())
    client = _client(monkeypatch, authority)
    payload = {
        "action": "admin.member.roles.change",
        "target_type": "member",
        "target_id": "alice-id",
        "target_version": "0:payload-digest",
        "meaning": "approved",
        "justification": "Approved after review",
    }

    response = client.post("/api/v1/signatures", json=payload)

    assert response.status_code == 201
    assert response.json()["action"] == "admin.member.roles.change"
    assert response.json()["target_version"] == "0:payload-digest"
    assert response.json()["authentication_assurance"] == "mfa"
    assert len(authority.signatures("admin-id")) == 1


def test_signature_idempotency_replays_only_the_same_actor_and_intent(monkeypatch) -> None:
    authority = IdentityAuthority(InMemorySettingsService())
    client = _client(monkeypatch, authority)
    payload = {
        "action": "staging.promote",
        "target_type": "promotion",
        "target_id": "feature-to-main",
        "target_version": "source:target:merge-digest",
        "meaning": "approved",
        "justification": "Promotion reviewed",
    }
    headers = {"Idempotency-Key": "persistent-signature-key"}

    created = client.post("/api/v1/signatures", json=payload, headers=headers)
    replayed = client.post("/api/v1/signatures", json=payload, headers=headers)

    assert created.status_code == replayed.status_code == 201
    assert replayed.json()["signature_id"] == created.json()["signature_id"]
    assert len(authority.signatures("admin-id")) == 1

    request = SignatureRequest(
        signer_subject="admin-id",
        meaning=SignatureMeaning.APPROVED,
        action=payload["action"],
        record_type=payload["target_type"],
        record_id=payload["target_id"],
        record_version=payload["target_version"],
        justification=payload["justification"],
    )
    assert authority.consume_signature(created.json()["signature_id"], request) is True
    consumed_replay = client.post("/api/v1/signatures", json=payload, headers=headers)
    assert consumed_replay.json()["signature_id"] == created.json()["signature_id"]
    assert consumed_replay.json()["consumed_at"] is not None

    changed = client.post(
        "/api/v1/signatures",
        json={**payload, "target_version": "different-merge-digest"},
        headers=headers,
    )
    assert changed.status_code == 409

    other_actor = AuthPrincipal(
        subject="other-admin",
        principal_type="user",
        groups=("admin",),
        claims={"auth_time": time.time(), "amr": ["mfa"]},
    )
    other_session = AuthenticatedSession(
        principal=other_actor,
        auth_method="bearer_token",
        provider_name="jwt",
        attributes={
            "jwt_issuer": "https://issuer.example",
            "jwt_audience": "phlo-api",
            "jwt_issuer_validated": "true",
            "jwt_audience_validated": "true",
        },
    )
    monkeypatch.setattr(
        v1_admin_identity,
        "authenticate_request",
        lambda _request: AuthResult(
            authenticated=True, principal=other_actor, session=other_session
        ),
    )
    other = client.post("/api/v1/signatures", json=payload, headers=headers)
    assert other.status_code == 201
    assert other.json()["signature_id"] != created.json()["signature_id"]


def test_signature_idempotency_does_not_bypass_failed_verification(monkeypatch) -> None:
    authority = IdentityAuthority(InMemorySettingsService())
    client = _client(monkeypatch, authority)
    payload = {
        "action": "branch.merge",
        "target_type": "branch",
        "target_id": "feature",
        "target_version": "merge-digest",
        "meaning": "approved",
        "justification": "Merge reviewed",
    }
    headers = {"Idempotency-Key": "merge-signature-key"}
    created = client.post("/api/v1/signatures", json=payload, headers=headers)
    assert created.status_code == 201

    unverified_session = AuthenticatedSession(
        principal=AuthPrincipal(
            subject="admin-id",
            principal_type="user",
            claims={"auth_time": time.time(), "amr": ["mfa"]},
        ),
        auth_method="bearer_token",
        provider_name="static",
    )
    monkeypatch.setattr(
        v1_admin_identity,
        "authenticate_request",
        lambda _request: AuthResult(
            authenticated=True,
            principal=unverified_session.principal,
            session=unverified_session,
        ),
    )

    rejected = client.post("/api/v1/signatures", json=payload, headers=headers)
    assert rejected.status_code == 403
    assert len(authority.signatures("admin-id")) == 1


def test_signature_api_rejects_static_sessions_even_with_claims(monkeypatch) -> None:
    authority = IdentityAuthority(InMemorySettingsService())
    client = _client(monkeypatch, authority)
    actor = AuthPrincipal(
        subject="admin-id",
        principal_type="user",
        claims={"auth_time": time.time(), "amr": ["mfa"]},
    )
    session = AuthenticatedSession(
        principal=actor,
        auth_method="bearer_token",
        provider_name="static",
        attributes={
            "jwt_issuer": "https://issuer.example",
            "jwt_audience": "phlo-api",
            "jwt_issuer_validated": "true",
            "jwt_audience_validated": "true",
        },
    )
    monkeypatch.setattr(
        v1_admin_identity,
        "authenticate_request",
        lambda _request: AuthResult(authenticated=True, principal=actor, session=session),
    )

    response = client.post(
        "/api/v1/signatures",
        json={
            "action": "admin.member.roles.change",
            "target_type": "member",
            "target_id": "alice-id",
            "target_version": "1:roles-digest",
            "meaning": "approved",
            "justification": "Approved after review",
        },
    )

    assert response.status_code == 403
    assert authority.signatures("admin-id") == []


def test_invitation_token_is_returned_once_and_bound_to_authenticated_email(monkeypatch) -> None:
    authority = IdentityAuthority(InMemorySettingsService())
    client = _client(monkeypatch, authority)
    email = "alice@example.com"
    version = v1_admin_identity._payload_version(0, {"email": email, "roles": ["analyst"]})
    signature = _sign(
        authority,
        action="admin.invitation.create",
        target_type="invitation",
        target_id=email,
        target_version=version,
    )
    response = client.post(
        "/api/v1/admin/invitations",
        json={"email": email, "roles": ["analyst"], "signature_id": signature.signature_id},
    )

    assert response.status_code == 201
    token = response.json()["token"]
    assert token
    assert all("token" not in item.__dict__ for item in authority.invitations())

    alice = AuthPrincipal(subject="alice-id", principal_type="user", email="ALICE@example.com")
    monkeypatch.setattr(v1_admin_identity, "get_request_principal", lambda _request: alice)
    accepted = client.post("/api/v1/invitations/accept", json={"token": token})
    assert accepted.status_code == 200
    assert accepted.json()["roles"] == ["analyst"]
    assert client.post("/api/v1/invitations/accept", json={"token": token}).status_code == 409


def test_identity_storage_outage_fails_closed(monkeypatch) -> None:
    client = _client(monkeypatch, IdentityAuthority(InMemorySettingsService()))

    def unavailable():
        raise StorageUnavailableError("Settings storage is unavailable")

    monkeypatch.setattr(v1_admin_identity, "_authority", unavailable)
    response = client.get("/api/v1/admin/members")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "backend_unavailable"


def test_role_catalog_reports_loaded_rules_without_condition_values(monkeypatch) -> None:
    client = _client(monkeypatch, IdentityAuthority(InMemorySettingsService()))
    backend = DefaultAuthorizationPolicyBackend(
        policies=[
            {
                "policy_id": "conditional-query",
                "effect": "allow",
                "action": "dataset.query",
                "principal": {"roles": ["analyst"], "attributes": {"tenant": "private-tenant"}},
                "resource": {
                    "type": "dataset",
                    "id_pattern": "sales.*",
                    "attributes": {"class": "private-class"},
                },
            },
            {
                "policy_id": "public-read",
                "effect": "allow",
                "action": "dataset.read",
                "resource": {"type": "dataset", "id_pattern": "public.*"},
            },
        ]
    )
    monkeypatch.setattr(v1_admin_identity, "get_authorization_backend", lambda: backend)
    response = client.get("/api/v1/admin/roles")
    assert response.status_code == 200
    catalog = response.json()
    assert catalog["assignment_scope"] == "installation"
    assert catalog["policy_status"] == "available"
    roles = {item["name"]: item["policies"] for item in catalog["items"]}
    assert set(roles) == {"admin", "operator", "developer", "analyst", "viewer", "service"}
    assert [policy["policy_id"] for policy in roles["analyst"]] == [
        "conditional-query",
        "public-read",
    ]
    assert [policy["policy_id"] for policy in roles["viewer"]] == ["public-read"]
    assert roles["analyst"][0]["principal_attribute_names"] == ["tenant"]
    assert roles["analyst"][0]["resource_attribute_names"] == ["class"]
    assert "private-tenant" not in response.text
    assert "private-class" not in response.text
    monkeypatch.setattr(v1_admin_identity, "get_authorization_backend", lambda: object())
    unavailable = client.get("/api/v1/admin/roles").json()
    assert unavailable["policy_status"] == "unavailable"
    assert {item["name"] for item in unavailable["items"]} == set(roles)
    assert all(item["policies"] == [] for item in unavailable["items"])


def test_cli_guided_assignment_preserves_multiple_roles_and_rejects_stale_changes(
    monkeypatch,
) -> None:
    authority = IdentityAuthority(InMemorySettingsService())
    authority.assign_roles(
        subject="alice-id",
        email="alice@example.com",
        principal_type="user",
        roles=["analyst", "viewer"],
        expected_version=0,
    )
    client = _client(monkeypatch, authority)
    monkeypatch.setattr(v1_admin_identity, "get_authorization_backend", lambda: None)
    monkeypatch.setattr(authz_identity, "_client", lambda _ctx: client)
    runner = CliRunner()
    prefix = ["--api-url", "https://api.example"]
    env = {"PHLO_AUTHZ_TOKEN": "test-session-not-a-login-proof"}
    # Accepting the default complete set must not discard the second role.
    result = runner.invoke(
        authz_identity.identity_group,
        [*prefix, "assign", "alice-id"],
        input="\ny\nAccess reviewed\n",
        env=env,
    )
    assert result.exit_code == 0, result.output
    assert authority.member("alice-id").roles == ("analyst", "viewer")
    assert authority.member("alice-id").version == 2
    assert authority.member("alice-id").email == "alice@example.com"
    stale = runner.invoke(
        authz_identity.identity_group,
        [
            *prefix,
            "assign",
            "alice-id",
            "--role",
            "admin",
            "--expected-version",
            "1",
            "--yes",
            "--justification",
            "Reviewed stale request",
        ],
        env=env,
    )
    assert stale.exit_code != 0
    assert "409" in stale.output
    assert authority.member("alice-id").roles == ("analyst", "viewer")
    invalid = runner.invoke(
        authz_identity.identity_group,
        [
            *prefix,
            "assign",
            "alice-id",
            "--role",
            "superuser",
            "--yes",
            "--justification",
            "Unknown role",
        ],
        env=env,
    )
    assert invalid.exit_code != 0
    assert "supported role" in invalid.output
    assert authority.member("alice-id").version == 2


@pytest.mark.parametrize(
    "url", ["http://api.example", "https://user:secret@api.example", "https://api.example?env=dev"]
)
def test_role_cli_rejects_unsafe_destinations(url) -> None:
    result = CliRunner().invoke(
        authz_identity.identity_group,
        ["--api-url", url, "roles"],
        env={"PHLO_AUTHZ_TOKEN": "do-not-leak"},
    )
    assert result.exit_code != 0
    assert "do-not-leak" not in result.output
