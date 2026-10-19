"""Feature readiness is authenticated, redacted, read-only and refreshable."""

from __future__ import annotations

import json
from pathlib import Path

import psycopg2
import pytest
from testcontainers.postgres import PostgresContainer

from phlo_api.api import v1_readiness
from phlo_api.errors import BackendUnavailableError
from security_test_support import authenticated_client


@pytest.fixture
def readiness_client(monkeypatch):
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        json.dumps(
            {
                "prod": {"dagster_location": "jobs", "nessie_ref": "main"},
                "staging": {"dagster_location": "other", "nessie_ref": "candidate"},
            }
        ),
    )
    monkeypatch.delenv("PHLO_RUN_EVIDENCE_DB_URL", raising=False)
    monkeypatch.delenv("PHLO_V1_PREVIEW_CATALOGS", raising=False)

    async def locations():
        return {"jobs"}

    monkeypatch.setattr(v1_readiness, "_locations", locations)
    return authenticated_client("viewer")


def test_readiness_distinguishes_missing_configuration_location_and_outage(
    readiness_client, monkeypatch
):
    client = readiness_client
    response = client.get("/api/v1/readiness?env=prod", headers={"x-request-id": "readiness-proof"})
    assert response.status_code == 200
    body = response.json()
    assert body["correlation_id"] == response.headers["x-request-id"] == "readiness-proof"
    checks = body["capabilities"]
    assert checks["orchestration"][0]["state"] == "ready"
    assert checks["incidents"][0]["reason_code"] == "configuration_missing"
    assert checks["query_preview"][0]["reason_code"] == "intentionally_disabled"
    assert (
        client.get("/api/v1/readiness?env=staging").json()["capabilities"]["orchestration"][0][
            "reason_code"
        ]
        == "code_location_unavailable"
    )

    async def outage():
        raise BackendUnavailableError("password=private SELECT secret FROM internal")

    monkeypatch.setattr(v1_readiness, "_locations", outage)
    response = client.get(
        "/api/v1/readiness?env=prod", headers={"x-request-id": "password=private"}
    )
    assert response.status_code == 200
    assert (
        response.json()["capabilities"]["orchestration"][0]["reason_code"] == "backend_unavailable"
    )
    assert "private" not in response.text
    assert "SELECT" not in response.text
    assert client.get("/health").json() == {"status": "healthy"}
    assert (
        client.get("/api/v1/readiness?env=prod", headers={"x-test-principal": ""}).status_code
        == 401
    )
    assert client.get("/api/v1/readiness?env=removed").status_code == 422
    monkeypatch.setenv("PHLO_V1_ENVIRONMENTS", "not-json")
    invalid = client.get("/api/v1/readiness?env=prod")
    assert invalid.status_code == 200
    check = invalid.json()["capabilities"]["environment"][0]
    assert check["reason_code"] == "configuration_invalid"
    assert "phlo.yaml" in check["remediation"]
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        json.dumps({"analytics": {"dagster_location": "jobs", "nessie_ref": "main"}}),
    )

    async def recovered_locations():
        return {"jobs"}

    monkeypatch.setattr(v1_readiness, "_locations", recovered_locations)
    recovered = client.get("/api/v1/readiness?env=analytics")
    assert recovered.status_code == 200
    assert recovered.json()["capabilities"]["orchestration"][0]["state"] == "ready"


def test_preview_probe_never_promotes_configuration_to_ready(readiness_client, monkeypatch):
    monkeypatch.setattr(
        v1_readiness,
        "preview_preflight",
        lambda env: [
            {
                "state": "configured",
                "reason_code": "enforcement_unverified",
                "message": "Local configuration is not proof of enforcement.",
                "remediation": "Verify the deployment.",
            }
        ],
    )
    monkeypatch.setattr(v1_readiness, "preview_catalog", lambda env, ref: "preview")
    result = {"rows": []}

    async def execute(*args, **kwargs):
        return result

    monkeypatch.setattr(v1_readiness, "execute_preview", execute)
    path = "/api/v1/readiness?env=prod"
    unavailable = readiness_client.get(path).json()["capabilities"]["query_preview"]
    assert unavailable[-1]["reason_code"] == "backend_unavailable"
    result["rows"] = [{"phlo_readiness": 1}]
    reachable = readiness_client.get(path).json()["capabilities"]["query_preview"]
    assert reachable[-1]["reason_code"] == "backend_reachable"
    assert all(check["state"] == "configured" for check in reachable)
    assert reachable[0]["reason_code"] == "enforcement_unverified"


@pytest.mark.integration
def test_storage_schema_outage_and_recovery_without_api_restart(readiness_client, monkeypatch):
    client = readiness_client
    with PostgresContainer("postgres:16", password="test") as postgres:
        dsn = postgres.get_connection_url(driver=None)
        monkeypatch.setenv("PHLO_RUN_EVIDENCE_DB_URL", dsn)
        path = "/api/v1/readiness?env=prod"
        missing = client.get(path)
        assert missing.status_code == 200
        assert missing.json()["capabilities"]["incidents"][0]["reason_code"] == "schema_missing"
        connection = psycopg2.connect(dsn)
        try:
            with connection, connection.cursor() as cursor:
                sql_dir = Path(v1_readiness.__file__).parent.parent / "sql"
                for name in (
                    "001_incidents.sql",
                    "002_query_usage.sql",
                    "003_incident_query_parity.sql",
                ):
                    cursor.execute((sql_dir / name).read_text(encoding="utf-8"))
                cursor.execute(
                    "INSERT INTO phlo.incident (incident_id,env,asset_id,kind,title,status) VALUES ('proof','prod','bronze/orders','schema','Proof','open')"
                )
            ready = client.get(path).json()["capabilities"]
            assert ready["incidents"][0]["state"] == "ready"
            assert ready["query_usage"][0]["state"] == "ready"
            with connection, connection.cursor() as cursor:
                cursor.execute("SELECT incident_id, title FROM phlo.incident")
                assert cursor.fetchall() == [("proof", "Proof")]
                cursor.execute("DROP TABLE phlo.asset_query_usage")
            partial = client.get(path).json()["capabilities"]
            assert partial["incidents"][0]["state"] == "ready"
            assert partial["query_usage"][0]["reason_code"] == "schema_missing"
            monkeypatch.setenv(
                "PHLO_RUN_EVIDENCE_DB_URL", "postgresql://private:secret@127.0.0.1:1/missing"
            )
            unavailable = client.get(path)
            assert (
                unavailable.json()["capabilities"]["incidents"][0]["reason_code"]
                == "backend_unavailable"
            )
            assert "secret" not in unavailable.text
            assert "postgresql" not in unavailable.text
            monkeypatch.setenv("PHLO_RUN_EVIDENCE_DB_URL", dsn)
            assert client.get(path).json()["capabilities"]["incidents"][0]["state"] == "ready"
        finally:
            connection.close()
