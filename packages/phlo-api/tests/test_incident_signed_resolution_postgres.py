"""End-to-end signed incident resolution against disposable PostgreSQL."""

from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from testcontainers.postgres import PostgresContainer

from phlo.capabilities.interfaces import AuthPrincipal, AuthResult, AuthenticatedSession
from phlo.identity.authority import IdentityAuthority
from phlo_api import incidents
from phlo_api.api import v1_admin_audit, v1_admin_identity
from phlo_postgres.settings_store import PostgresSettingsStore


@pytest.mark.integration
def test_resolution_is_signed_versioned_environment_bound_audited_and_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with PostgresContainer("postgres:18-alpine") as postgres:
        dsn = postgres.get_connection_url(driver=None)
        monkeypatch.setenv("PHLO_RUN_EVIDENCE_DB_URL", dsn)
        monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_DB_URL", dsn)
        monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_BACKEND", "postgres")
        monkeypatch.setenv("PHLO_IDENTITY_AUTHORITY_ENABLED", "1")
        monkeypatch.setenv("PHLO_SIGNATURE_HMAC_KEY", "incident-test-signature-key")
        monkeypatch.setenv("PHLO_AUDIT_HMAC_KEY", "incident-test-audit-key")
        store = PostgresSettingsStore()
        monkeypatch.setattr("phlo.identity.authority.get_settings_service", lambda: store)
        actor = AuthPrincipal(
            subject="operator-id",
            principal_type="user",
            issuer="test-idp",
            groups=("operator",),
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
        monkeypatch.setattr(incidents, "get_request_principal", lambda _request: actor)
        authority = IdentityAuthority(store)
        monkeypatch.setattr(v1_admin_identity, "_authority", lambda: authority)
        monkeypatch.setattr(
            v1_admin_identity,
            "authenticate_request",
            lambda _request: AuthResult(authenticated=True, principal=actor, session=session),
        )
        incidents.initialize_incidents()

        app = FastAPI()
        app.include_router(incidents.router, prefix="/api/v1")
        app.include_router(v1_admin_identity.router, prefix="/api/v1")
        app.include_router(v1_admin_audit.router, prefix="/api/v1")
        client = TestClient(app)

        def create_incident(env: str) -> dict[str, object]:
            response = client.post(
                "/api/v1/incidents",
                params={"env": env},
                headers={"Idempotency-Key": f"create-{env}"},
                json={
                    "asset_id": "warehouse/orders",
                    "kind": "failed_check",
                    "title": f"{env} orders check failed",
                    "evidence": {"check_id": "orders.pk", "passed": False},
                    "evidence_id": f"{env}:orders.pk",
                },
            )
            assert response.status_code == 201
            return response.json()

        prod = create_incident("prod")
        staging = create_incident("staging")
        for env, incident in (("prod", prod), ("staging", staging)):
            acknowledged = client.patch(
                f"/api/v1/incidents/{incident['id']}",
                params={"env": env},
                headers={"If-Match": "1", "Idempotency-Key": f"ack-{env}"},
                json={"status": "acknowledged"},
            )
            assert acknowledged.status_code == 200

        comment = "Validated the recovery and verified the source data."

        def issue_signature(env: str, incident_id: str, version: str) -> str:
            response = client.post(
                "/api/v1/signatures",
                json={
                    "action": "incident.resolve",
                    "target_type": "incident",
                    "target_id": f"{env}:{incident_id}",
                    "target_version": version,
                    "meaning": "approved",
                    "justification": comment,
                },
            )
            assert response.status_code == 201
            assert response.json()["authentication_assurance"] == "mfa"
            return response.json()["signature_id"]

        cross_environment = issue_signature("staging", str(prod["id"]), "2")
        denied = client.patch(
            f"/api/v1/incidents/{prod['id']}",
            params={"env": "prod"},
            headers={"If-Match": "2", "Idempotency-Key": "resolve-wrong-env"},
            json={
                "status": "resolved",
                "comment": comment,
                "signature_id": cross_environment,
            },
        )
        assert denied.status_code == 409

        stale = issue_signature("prod", str(prod["id"]), "1")
        stale_response = client.patch(
            f"/api/v1/incidents/{prod['id']}",
            params={"env": "prod"},
            headers={"If-Match": "2", "Idempotency-Key": "resolve-stale-signature"},
            json={"status": "resolved", "comment": comment, "signature_id": stale},
        )
        assert stale_response.status_code == 409

        signature = issue_signature("prod", str(prod["id"]), "2")
        resolved = client.patch(
            f"/api/v1/incidents/{prod['id']}",
            params={"env": "prod"},
            headers={"If-Match": "2", "Idempotency-Key": "resolve-prod"},
            json={"status": "resolved", "comment": comment, "signature_id": signature},
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "resolved"
        assert resolved.json()["version"] == 3

        replay = client.patch(
            f"/api/v1/incidents/{prod['id']}",
            params={"env": "prod"},
            headers={"If-Match": "2", "Idempotency-Key": "resolve-prod"},
            json={"status": "resolved", "comment": comment, "signature_id": signature},
        )
        assert replay.status_code == 200
        assert replay.json() == resolved.json()

        staging_detail = client.get(f"/api/v1/incidents/{staging['id']}", params={"env": "staging"})
        assert staging_detail.status_code == 200
        assert staging_detail.json()["status"] == "acknowledged"

        verified = client.get("/api/v1/admin/audit/verify", params={"surface": "phlo-api"})
        assert verified.status_code == 200
        assert verified.json()["valid"] is True
        records = client.get(
            "/api/v1/admin/audit/records",
            params={"surface": "phlo-api", "action": "incident.resolve"},
        )
        assert records.status_code == 200
        assert any(
            item["event"]["resource_id"] == f"prod:{prod['id']}" for item in records.json()["items"]
        )
        signature_events = client.get(
            "/api/v1/admin/audit/records",
            params={"surface": "phlo-api", "action": "signature.create"},
        )
        assert signature_events.status_code == 200
        assert len(signature_events.json()["items"]) == 6
