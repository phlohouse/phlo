"""Query-selected catalog identity and durable, scoped observed usage."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from testcontainers.postgres import PostgresContainer

from phlo.capabilities import AuthPrincipal, AuthorizationDecision, Principal
from phlo_api import security_manifest, usage
from phlo_api.api import v1_assets
from phlo_api.errors import BackendUnavailableError
from phlo_api.main import app


def _properties(ref: str, uri: str = "http://nessie:19120/api/v2") -> dict[str, str]:
    return {
        "connector.name": "iceberg",
        "iceberg.catalog.type": "nessie",
        "iceberg.nessie-catalog.uri": uri,
        "iceberg.nessie-catalog.ref": ref,
        "iceberg.nessie-catalog.default-warehouse-dir": "s3://lake/warehouse",
        "fs.native-s3.enabled": "true",
        "s3.endpoint": "http://minio:9000",
        "s3.path-style-access": "true",
        "s3.region": "us-east-1",
    }


def _config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        json.dumps(
            {
                "prod": {"dagster_location": "prod_location", "nessie_ref": "main"},
                "staging": {"dagster_location": "stage_location", "nessie_ref": "candidate"},
            }
        ),
    )
    monkeypatch.setenv(
        "PHLO_V1_USAGE_TRINO_SOURCES",
        json.dumps(
            {
                "cluster": {
                    "service_subject": "trino_listener",
                    "catalogs": {
                        "prod": {"catalog": "lake_prod", "properties": _properties("main")},
                        "staging": {
                            "catalog": "lake_stage",
                            "properties": _properties("candidate"),
                        },
                    },
                }
            }
        ),
    )


def _event(query: str, catalog: str, version: str, state: str = "FINISHED") -> dict:
    return {
        "metadata": {"queryId": query, "queryState": state, "query": "private SQL"},
        "context": {"serverVersion": "483", "user": "private user"},
        "endTime": datetime.now(UTC).isoformat(),
        "ioMetadata": {
            "inputs": [
                {
                    "catalogName": catalog,
                    "catalogVersion": version,
                    "schema": "warehouse",
                    "table": "orders",
                    "connectorName": "iceberg",
                    "connectorInfo": {"private": "discard"},
                }
            ]
        },
    }


def test_catalog_version_matches_disposable_trino_483_query_input() -> None:
    # Independent observed value from a real Trino 483 query against Nessie 0.108.3.
    properties = _properties("main", "http://phlo-usage-nessie:19120/api/v2")
    properties["s3.endpoint"] = "http://phlo-usage-minio:9000"
    assert usage.catalog_version("usage", properties) == (
        "8e1bb9028eaadb74714c5ffe5e33d25926b93e64225fb05a840b4d0296061a34"
    )
    assert usage.catalog_version(
        "usage", {**properties, "iceberg.nessie-catalog.ref": "other"}
    ) != (usage.catalog_version("usage", properties))


def test_rest_catalog_and_unresolved_ref_cannot_claim_trusted_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(monkeypatch)
    config = json.loads(os.environ["PHLO_V1_USAGE_TRINO_SOURCES"])
    config["cluster"]["catalogs"]["prod"]["properties"]["iceberg.catalog.type"] = "rest"
    monkeypatch.setenv("PHLO_V1_USAGE_TRINO_SOURCES", json.dumps(config))
    with pytest.raises(BackendUnavailableError, match="Verified Trino usage sources"):
        usage._sources()
    config["cluster"]["catalogs"]["prod"]["properties"]["iceberg.catalog.type"] = "nessie"
    config["cluster"]["catalogs"]["prod"]["properties"]["iceberg.nessie-catalog.ref"] = (
        "${ENV:PHLO_REF}"
    )
    monkeypatch.setenv("PHLO_V1_USAGE_TRINO_SOURCES", json.dumps(config))
    with pytest.raises(BackendUnavailableError, match="Verified Trino usage sources"):
        usage._sources()


def test_mixed_default_catalog_does_not_hide_verified_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _config(monkeypatch)
    bindings = usage._sources()["cluster"][1]
    version = usage.catalog_version("lake_prod", _properties("main"))
    event = _event("q_mixed", "lake_prod", version)
    event["ioMetadata"]["inputs"].append(
        {
            "catalogName": "iceberg",
            "catalogVersion": "default",
            "schema": "warehouse",
            "table": "orders",
        }
    )
    _query, _time, _state, matches = usage._completed_event(event, bindings)
    assert matches == [("lake_prod", version, "prod", "main", "warehouse/orders")]


@pytest.mark.integration
def test_verified_query_usage_postgres_http_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    with PostgresContainer("postgres:18-alpine") as postgres:
        _config(monkeypatch)
        monkeypatch.setenv("PHLO_RUN_EVIDENCE_DB_URL", postgres.get_connection_url(driver=None))
        usage.initialize_usage()
        principal = AuthPrincipal(subject="trino_listener", principal_type="service")
        monkeypatch.setattr(security_manifest, "get_request_principal", lambda _request: principal)
        monkeypatch.setattr(usage, "get_request_principal", lambda _request: principal)
        monkeypatch.setattr(
            security_manifest,
            "resolve_request_principal",
            lambda *_args, **_kwargs: Principal(
                subject="trino_listener", principal_type="service", roles=("listener",)
            ),
        )
        monkeypatch.setattr(security_manifest, "is_regulated", lambda: False)
        decisions: list[tuple[str, str]] = []

        class Backend:
            allowed = True

            def explain_decision(self, _principal, action, resource, _context):
                decisions.append((action, resource.resource_id))
                return AuthorizationDecision(
                    allowed=self.allowed,
                    reason_code="explicit_allow" if self.allowed else "default_deny",
                )

        backend = Backend()
        monkeypatch.setattr(security_manifest, "get_authorization_backend", lambda: backend)

        async def assets(_request, _env, *, allowed_query):
            return [
                SimpleNamespace(
                    id="order_current_state", relation="warehouse.orders", history_scoped=True
                ),
                SimpleNamespace(
                    id="warehouse/orders", relation="warehouse.other", history_scoped=True
                ),
                SimpleNamespace(id="raw_input", relation=None, history_scoped=True),
            ]

        monkeypatch.setattr(v1_assets, "_assets", assets)
        prod_hash = usage.catalog_version("lake_prod", _properties("main"))
        stage_hash = usage.catalog_version("lake_stage", _properties("candidate"))
        event_url = "/api/v1/trino/query-completed?source_id=cluster"
        prod_url = "/api/v1/assets/order_current_state/query-usage?env=prod&limit=1"
        stage_url = "/api/v1/assets/order_current_state/query-usage?env=staging"
        with TestClient(app) as http:
            assert http.post(event_url, json=_event("q_prod_a", "lake_prod", prod_hash)).json() == {
                "observed_inputs": 1
            }
            assert http.post(
                event_url, json=_event("q_stage", "lake_stage", stage_hash)
            ).json() == {"observed_inputs": 1}
            extra = _event("q_prod_b", "lake_prod", prod_hash)
            assert http.post(event_url, json=extra).status_code == 202
            assert http.post(event_url, json=extra).json() == {"observed_inputs": 0}
            assert (
                http.post(event_url, json=_event("q_prod_a", "lake_stage", stage_hash)).status_code
                == 409
            )
            assert http.post(
                event_url, json=_event("q_failed", "lake_prod", prod_hash, "FAILED")
            ).json() == {"observed_inputs": 0}
            assert http.post(event_url, json=_event("q_wrong", "lake_prod", "a" * 64)).json() == {
                "observed_inputs": 0
            }
            old = _event("q_expired", "lake_prod", prod_hash)
            old["endTime"] = (datetime.now(UTC) - timedelta(days=31)).isoformat()
            assert http.post(event_url, json=old).json() == {"observed_inputs": 0}
            malformed = _event("q_malformed", "lake_prod", prod_hash)
            malformed["ioMetadata"]["inputs"][0]["schema"] = ""
            assert http.post(event_url, json=malformed).status_code == 422
            stage = http.get(stage_url)
            assert stage.status_code == 200
            assert [row["query_id"] for row in stage.json()["items"]] == ["q_stage"]
            prod = http.get(prod_url)
            assert prod.status_code == 200
            assert prod.json()["status"] == "partial"
            assert prod.json()["table_name"] == "warehouse.orders"
            assert (
                http.get("/api/v1/assets/warehouse/orders/query-usage?env=prod").json()["items"]
                == []
            )
            missing = http.get("/api/v1/assets/raw_input/query-usage?env=prod").json()
            assert missing["status"] == "unavailable" and missing["reason"] == "no_asset_relation"
            page_2 = http.get(prod_url + "&cursor=" + prod.json()["next_cursor"])
            assert page_2.status_code == 200
            assert {row["query_id"] for row in prod.json()["items"] + page_2.json()["items"]} == {
                "q_prod_a",
                "q_prod_b",
            }
            assert http.get(stage_url + "&cursor=" + prod.json()["next_cursor"]).status_code == 400
            assert http.get(prod_url.replace("order_current_state", "unknown")).status_code == 404
            assert http.post(event_url, content=b"x" * 262_145).status_code == 413
            old_map = os.environ["PHLO_V1_ENVIRONMENTS"]
            new_map = json.loads(old_map)
            new_map["prod"]["nessie_ref"] = "release-next"
            monkeypatch.setenv("PHLO_V1_ENVIRONMENTS", json.dumps(new_map))
            assert http.get(prod_url).json()["status"] == "unavailable"
            assert http.get(stage_url).json()["items"][0]["query_id"] == "q_stage"
            monkeypatch.setenv("PHLO_V1_ENVIRONMENTS", old_map)
            backend.allowed = False
            assert (
                http.post(event_url, json=_event("q_denied", "lake_prod", prod_hash)).status_code
                == 403
            )
            backend.allowed = True
            monkeypatch.setattr(
                usage,
                "get_request_principal",
                lambda _request: AuthPrincipal(subject="other", principal_type="service"),
            )
            assert http.post(event_url, json=extra).status_code == 403
            assert ("service.manage", "source_id=cluster") in decisions
            assert ("asset.read", "env=prod|asset_id=order_current_state") in decisions
        with usage._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM phlo.asset_query_usage ORDER BY query_id")
            rows = cursor.fetchall()
            assert len(rows) == 3
            assert all(row[4] in {"main", "candidate"} for row in rows)
            cursor.execute("SELECT event_digest FROM phlo.query_usage_event")
            assert all("private" not in value[0] for value in cursor.fetchall())
