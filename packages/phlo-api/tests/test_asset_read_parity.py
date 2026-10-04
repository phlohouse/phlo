"""Asset read predicates and guarded rollback against disposable Iceberg data."""

import json
import sqlite3
import time
from dataclasses import replace

import pyarrow as pa
import pytest
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.schema import Schema
from pyiceberg.types import LongType, NestedField

from phlo.capabilities.interfaces import (
    AuthPrincipal,
    AuthResult,
    AuthenticatedSession,
    AuthorizationDecision,
)
from phlo.plugins import observatory_settings
from phlo_api.api import authentication, operation_controls, v1_assets
from phlo_api.api.asset_preview_filters import preview_filters, preview_where
from phlo_api.errors import BadGatewayError
from phlo_iceberg import catalog as iceberg_catalog, tables
from test_v1_api import _asset_inventory_response, client as client


@pytest.mark.parametrize(
    "operator,expected",
    [
        ("eq", [3]),
        ("ne", [-2, 8]),
        ("lt", [-2]),
        ("lte", [-2, 3]),
        ("gt", [8]),
        ("gte", [3, 8]),
        ("is_null", [None]),
        ("is_not_null", [-2, 3, 8]),
    ],
)
def test_filter_operators_execute_independent_sql(operator, expected):
    item = {"column": "value", "operator": operator}
    if operator not in {"is_null", "is_not_null"}:
        item["value"] = 3
    where = preview_where(preview_filters.validate_python([item]), {"value"})
    with sqlite3.connect(":memory:") as db:
        db.execute('CREATE TABLE sample ("value" INTEGER)')
        db.executemany("INSERT INTO sample VALUES (?)", [(None,), (-2,), (3,), (8,)])
        assert [row[0] for row in db.execute("SELECT value FROM sample" + where)] == expected


def test_filter_quotes_identifiers_and_injection_values():
    column = 'odd"name; DROP TABLE sample; --'
    value = "O'Brien'; DROP TABLE sample; --"
    where = preview_where(
        preview_filters.validate_python(
            [
                {"column": column, "operator": "eq", "value": value},
            ]
        ),
        {column},
    )
    assert (
        where
        == " WHERE \"odd\"\"name; DROP TABLE sample; --\" = 'O''Brien''; DROP TABLE sample; --'"
    )
    with sqlite3.connect(":memory:") as db:
        db.execute('CREATE TABLE sample ("odd""name; DROP TABLE sample; --" TEXT)')
        db.executemany("INSERT INTO sample VALUES (?)", [(value,), ("other",)])
        assert list(db.execute("SELECT * FROM sample" + where)) == [(value,)]
        assert db.execute("SELECT COUNT(*) FROM sample").fetchone()[0] == 2


@pytest.mark.parametrize(
    "item",
    [
        {"column": "id", "operator": "eq OR TRUE", "value": 1},
        {"column": "id", "operator": "eq", "value": None},
        {"column": "id", "operator": "gte", "value": float("inf")},
        {"column": "id", "operator": "eq", "value": {"sql": "1 OR TRUE"}},
        {"column": "id", "operator": "is_null", "value": "ignored"},
        {"column": "id", "operator": "eq", "value": 1, "env": "prod"},
    ],
)
def test_filters_reject_sql_fragments_and_selector_smuggling(item):
    with pytest.raises(ValueError):
        preview_filters.validate_python([item])


def test_filter_schema_allowlist_is_not_a_quoting_fallback():
    with pytest.raises(ValueError, match="selected table"):
        preview_where(
            preview_filters.validate_python(
                [
                    {"column": "secret_from_other_environment", "operator": "eq", "value": 1},
                ]
            ),
            {"id"},
        )


@pytest.fixture
def rollback_data(client, monkeypatch, tmp_path):
    http, *_ = client
    monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_BACKEND", "memory")
    monkeypatch.setattr(observatory_settings, "_memory_service", None)
    catalogs = {}
    snapshots = {}
    for ref, values in (("main", [91, 92]), ("candidate", [7, 11])):
        catalog = SqlCatalog(
            ref, uri=f"sqlite:///{tmp_path}/{ref}.sqlite", warehouse=f"file://{tmp_path}/{ref}"
        )
        catalog.create_namespace("raw")
        table = catalog.create_table("raw.events", Schema(NestedField(1, "id", LongType())))
        table.append(pa.table({"id": pa.array([values[0]], type=pa.int64())}))
        first = table.current_snapshot().snapshot_id
        table.append(pa.table({"id": pa.array([values[1]], type=pa.int64())}))
        snapshots[ref] = (first, table.current_snapshot().snapshot_id, table.metadata_location)
        catalogs[ref] = catalog
    monkeypatch.setattr(tables, "get_catalog", lambda ref: catalogs[ref])
    monkeypatch.setattr(iceberg_catalog, "get_catalog", lambda ref: catalogs[ref])
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    actor = AuthPrincipal(
        subject="alice",
        principal_type="user",
        claims={"auth_time": time.time(), "amr": ["pwd", "mfa"]},
    )
    session = AuthenticatedSession(
        principal=actor,
        auth_method="bearer_token",
        provider_name="jwt",
        attributes={
            "jwt_issuer": "https://test-issuer",
            "jwt_audience": "phlo-api",
            "jwt_issuer_validated": "true",
            "jwt_audience_validated": "true",
        },
    )
    monkeypatch.setattr(
        authentication,
        "authenticate_request",
        lambda request: AuthResult(authenticated=True, principal=actor, session=session),
    )
    monkeypatch.setattr(
        operation_controls,
        "require_scope",
        lambda request, scope: {"subject": "alice", "scopes": [scope]},
    )
    payload = {
        "snapshot_id": str(snapshots["candidate"][0]),
        "nessie_ref": "candidate",
        "expected_metadata_location": snapshots["candidate"][2],
        "confirmed": True,
        "idempotency_key": "rollback-test",
    }
    return http, catalogs, snapshots, payload, session, tmp_path


def test_rollback_changes_only_selected_ref_and_replays_audited_result(rollback_data):
    http, catalogs, snapshots, payload, _, tmp_path = rollback_data
    response = http.post("/api/v1/tables/raw.events/rollback?env=staging", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["rolled_back_to"] == payload["snapshot_id"]
    assert catalogs["candidate"].load_table("raw.events").scan().to_arrow()["id"].to_pylist() == [7]
    assert sorted(
        catalogs["main"].load_table("raw.events").scan().to_arrow()["id"].to_pylist()
    ) == [91, 92]
    replay = http.post("/api/v1/tables/raw.events/rollback?env=staging", json=payload)
    assert replay.json() == response.json()
    records = (tmp_path / ".phlo/audit/operations.jsonl").read_text().splitlines()
    assert len(records) == 1
    assert (
        json.loads(records[0])["payload"]["expected_metadata_location"] == snapshots["candidate"][2]
    )
    changed = http.post(
        "/api/v1/tables/raw.events/rollback?env=staging",
        json={**payload, "snapshot_id": str(snapshots["candidate"][1])},
    )
    assert changed.status_code == 409


@pytest.mark.parametrize(
    "failure,status",
    [
        ("revision", 409),
        ("missing_revision", 422),
        ("ref", 409),
        ("snapshot", 422),
        ("confirmation", 422),
        ("missing_mfa", 403),
        ("stale_mfa", 403),
        ("unverified_issuer", 403),
        ("static_claims", 403),
        ("permission", 403),
        ("dataset_permission", 403),
        ("provider", 503),
    ],
)
def test_rollback_failure_never_changes_disposable_table(
    rollback_data, client, monkeypatch, failure, status
):
    http, catalogs, snapshots, payload, session, _ = rollback_data
    if failure == "revision":
        payload["expected_metadata_location"] = "stale.json"
    if failure == "missing_revision":
        del payload["expected_metadata_location"]
    if failure == "ref":
        payload["nessie_ref"] = "main"
    if failure == "snapshot":
        payload["snapshot_id"] = "1"
    if failure == "confirmation":
        payload["confirmed"] = False
    if failure == "missing_mfa":
        session.principal.claims["amr"] = ["pwd"]
    if failure == "stale_mfa":
        session.principal.claims["auth_time"] = time.time() - 301
    if failure == "unverified_issuer":
        session.attributes["jwt_issuer_validated"] = "false"
    if failure == "static_claims":
        static_session = replace(session, provider_name="static-token", attributes={})
        monkeypatch.setattr(
            authentication,
            "authenticate_request",
            lambda request: AuthResult(
                authenticated=True,
                principal=static_session.principal,
                session=static_session,
            ),
        )
    if failure == "dataset_permission":
        client[4].explain_decision = lambda *args: AuthorizationDecision(
            allowed=False, reason_code="explicit_deny"
        )
    if failure == "permission":
        from fastapi import HTTPException

        def deny(*args):
            raise HTTPException(status_code=403, detail="Scope denied")

        monkeypatch.setattr(operation_controls, "require_scope", deny)
    if failure == "provider":

        def unavailable(*args, **kwargs):
            raise OSError("offline")

        monkeypatch.setattr(tables, "rollback_table_to_snapshot", unavailable)
    response = http.post("/api/v1/tables/raw.events/rollback?env=staging", json=payload)
    assert response.status_code == status, response.text
    assert (
        catalogs["candidate"].load_table("raw.events").current_snapshot().snapshot_id
        == snapshots["candidate"][1]
    )
    assert sorted(
        catalogs["candidate"].load_table("raw.events").scan().to_arrow()["id"].to_pylist()
    ) == [7, 11]
    assert (
        catalogs["main"].load_table("raw.events").current_snapshot().snapshot_id
        == snapshots["main"][1]
    )


def test_snapshot_history_reports_actual_current_not_newest(rollback_data):
    http, catalogs, snapshots, payload, _, _ = rollback_data
    http.post("/api/v1/tables/raw.events/rollback?env=staging", json=payload).raise_for_status()
    response = http.get("/api/v1/tables/raw.events/snapshots?env=staging").json()
    assert response["current_snapshot_id"] == snapshots["candidate"][0]
    assert (
        response["metadata_location"]
        == catalogs["candidate"].load_table("raw.events").metadata_location
    )
    assert all(item["author"] is None for item in response["items"])
    assert response["items"][0]["snapshot_id"] == snapshots["candidate"][1]


def test_rollback_optimistic_commit_rejects_race_after_revision_check(rollback_data, monkeypatch):
    from types import SimpleNamespace
    from pyiceberg.exceptions import CommitFailedException

    _, catalogs, snapshots, payload, _, _ = rollback_data
    table = catalogs["candidate"].load_table("raw.events")
    concurrent = catalogs["candidate"].load_table("raw.events")
    concurrent.append(pa.table({"id": pa.array([17], type=pa.int64())}))
    monkeypatch.setattr(
        tables, "get_catalog", lambda ref: SimpleNamespace(load_table=lambda name: table)
    )
    with pytest.raises(CommitFailedException):
        tables.rollback_table_to_snapshot(
            "raw.events",
            snapshots["candidate"][0],
            "candidate",
            expected_metadata_location=payload["expected_metadata_location"],
        )
    assert sorted(
        catalogs["candidate"].load_table("raw.events").scan().to_arrow()["id"].to_pylist()
    ) == [7, 11, 17]


def test_protected_main_denies_api_and_provider_before_catalog_read(rollback_data, monkeypatch):
    http, _, snapshots, payload, _, _ = rollback_data
    observatory_settings.get_settings_service().put(
        observatory_settings.SettingsScope.GLOBAL,
        observatory_settings.ADMIN_SETTINGS_NAMESPACE,
        {"version": 1, "values": {"observatory.settings.audit.protect_main": True}},
    )

    def unexpected_catalog(ref):
        pytest.fail("Protected main must be rejected before catalog reads")

    monkeypatch.setattr(tables, "get_catalog", unexpected_catalog)
    with pytest.raises(PermissionError, match="protected"):
        tables.rollback_table_to_snapshot("raw.events", snapshots["main"][0], "main")
    response = http.post(
        "/api/v1/tables/raw.events/rollback?env=prod",
        json={
            **payload,
            "nessie_ref": "main",
            "snapshot_id": str(snapshots["main"][0]),
            "expected_metadata_location": snapshots["main"][2],
        },
    )
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("entry_type", ["text", "jsonString"])
def test_report_metadata_accepts_only_explicit_string_arrays(entry_type):
    node = {
        "assetKey": {"path": ["gold", "daily"]},
        "repository": {"location": {"name": "testing_jobs"}},
        "isMaterializable": True,
        "assetMaterializations": [],
        "dependencyKeys": [],
        "metadataEntries": [
            {"label": "phlo/reports", entry_type: '["Site daily report"]'},
        ],
    }
    assert v1_assets._asset_view(node, "candidate").reports == ["Site daily report"]
    node["metadataEntries"] = [{"label": "consumer", "text": "An arbitrary descendant"}]
    assert v1_assets._asset_view(node, "candidate").reports == []
    node["metadataEntries"] = [{"label": "phlo/reports", entry_type: '[{"url":"https://report"}]'}]
    with pytest.raises(BadGatewayError, match="Invalid declared report metadata"):
        v1_assets._asset_view(node, "candidate")


def test_preview_filters_use_selected_ref_schema_and_return_executed_sql(
    client, monkeypatch, tmp_path
):
    http, *_ = client
    calls = []
    schemas = []

    async def graphql(query, variables=None):
        return _asset_inventory_response(
            [
                {
                    "id": location,
                    "assetKey": {"path": ["events"]},
                    "repository": {"name": "repo", "location": {"name": location}},
                    "isMaterializable": True,
                    "assetMaterializations": [],
                    "dependencyKeys": [],
                    "metadataEntries": [{"label": "target_table", "text": "raw.events"}],
                }
                for location in ("testing_jobs",)
            ]
        )

    def schema(table, ref):
        schemas.append((table, ref))
        return 2, [
            {"schema_id": 1, "fields": [{"name": "retired"}]},
            {"schema_id": 2, "fields": [{"name": "site" if ref == "candidate" else "prod_only"}]},
        ]

    async def preview(sql, **kwargs):
        calls.append((sql, kwargs["catalog"]))
        return {"columns": [{"name": "site", "type": "varchar"}], "rows": [], "has_more": False}

    monkeypatch.setattr(v1_assets, "_graphql", graphql)
    monkeypatch.setattr(v1_assets, "_iceberg_schema", schema)
    monkeypatch.setattr(v1_assets, "execute_preview", preview)
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_V1_PREVIEW_SERVER_LIMITS_CONFIGURED", "1")
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD_STAGING", "test-only")
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD_PROD", "test-prod-only")
    monkeypatch.setenv(
        "PHLO_V1_PREVIEW_CATALOGS",
        json.dumps(
            {
                "staging": {"catalog": "stage_catalog", "nessie_ref": "candidate"},
                "prod": {"catalog": "prod_catalog", "nessie_ref": "main"},
            }
        ),
    )
    predicates = [{"column": "site", "operator": "eq", "value": "O'Brien'; DROP TABLE events; --"}]
    response = http.get(
        "/api/v1/assets/events/preview",
        params={"env": "staging", "filters": json.dumps(predicates)},
    )
    assert response.status_code == 200, response.text
    expected = 'SELECT * FROM "stage_catalog"."raw"."events" WHERE "site" = \'O\'\'Brien\'\'; DROP TABLE events; --\' LIMIT 21'
    assert response.json()["sql"] == expected
    assert calls == [(expected, "stage_catalog")]
    assert schemas == [("raw.events", "candidate")]
    for column in ("prod_only", "retired"):
        response = http.get(
            "/api/v1/assets/events/preview",
            params={"env": "staging", "filters": json.dumps([{**predicates[0], "column": column}])},
        )
        assert response.status_code == 422
    for selector in ("catalog", "nessie_ref", "sql"):
        response = http.get(
            "/api/v1/assets/events/preview", params={"env": "staging", selector: "prod"}
        )
        assert response.status_code == 400
    duplicate = http.get("/api/v1/assets/events/preview?env=staging&filters=[]&filters=[]")
    assert duplicate.status_code == 422
    assert len(calls) == 1


def test_detail_preserves_column_lineage_and_scoped_transitive_report_evidence(client, monkeypatch):
    http, *_ = client

    def node(name, dependencies=(), metadata=(), location="testing_jobs"):
        return {
            "id": name,
            "assetKey": {"path": [name]},
            "repository": {"name": "repo", "location": {"name": location}},
            "isMaterializable": True,
            "assetMaterializations": [],
            "dependencyKeys": [{"path": [key]} for key in dependencies],
            "metadataEntries": list(metadata),
        }

    selected = node(
        "events",
        metadata=[
            {
                "label": "dagster/column_lineage",
                "lineage": [
                    {
                        "columnName": "site",
                        "columnDeps": [
                            {
                                "assetKey": {"path": ["raw_events"]},
                                "columnName": "site_name",
                            }
                        ],
                    }
                ],
            }
        ],
    )
    inventory = [
        selected,
        node("intermediate", ["events"]),
        node(
            "daily_report",
            ["intermediate"],
            [{"label": "phlo/reports", "jsonString": '["Site daily report"]'}],
        ),
        node("unrelated", metadata=[{"label": "phlo/reports", "text": '["Unrelated report"]'}]),
        node(
            "prod_report",
            ["events"],
            [{"label": "phlo/reports", "text": '["Production report"]'}],
            "production_jobs",
        ),
    ]

    async def graphql(query, variables=None):
        if "V1Assets" in query:
            return _asset_inventory_response(inventory)
        return {"data": {"assetNodeOrError": {"__typename": "AssetNode", **selected}}}

    monkeypatch.setattr(v1_assets, "_graphql", graphql)
    response = http.get("/api/v1/assets/events?env=staging")
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["column_lineage"] == {
        "site": [{"asset_key": ["raw_events"], "column_name": "site_name"}]
    }
    assert [(asset["id"], asset["reports"]) for asset in detail["downstream"]] == [
        ("intermediate", []),
        ("daily_report", ["Site daily report"]),
    ]


def test_duplicate_keys_read_and_rollback_use_requested_definition_and_ref(
    rollback_data, monkeypatch
):
    http, catalogs, snapshots, payload, _, _ = rollback_data
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        json.dumps(
            {
                "prod": {"dagster_location": "iot_prod", "nessie_ref": "main"},
                "staging": {"dagster_location": "iot_staging", "nessie_ref": "dev"},
            }
        ),
    )
    catalogs["dev"] = catalogs["candidate"]
    monkeypatch.setenv("PHLO_V1_PREVIEW_SERVER_LIMITS_CONFIGURED", "1")
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD_STAGING", "test-only")
    monkeypatch.setenv("PHLO_V1_PREVIEW_TRINO_PASSWORD_PROD", "test-only-prod")
    monkeypatch.setenv(
        "PHLO_V1_PREVIEW_CATALOGS",
        json.dumps(
            {
                "prod": {"catalog": "prod_catalog", "nessie_ref": "main"},
                "staging": {"catalog": "staging_catalog", "nessie_ref": "dev"},
            }
        ),
    )

    def event(location, ref, timestamp):
        return {
            "timestamp": str(timestamp),
            "runId": location,
            "partition": None,
            "runOrError": {
                "__typename": "Run",
                "runId": location,
                "status": "SUCCESS",
                "tags": [{"key": "phlo/ref", "value": ref}],
                "repositoryOrigin": {"repositoryLocationName": location},
            },
        }

    events = [event("iot_prod", "main", 1790964100000), event("iot_staging", "dev", 1790964000000)]
    nodes = [
        {
            "id": location,
            "assetKey": {"path": ["events"]},
            "description": location,
            "repository": {"name": "repo", "location": {"name": location}},
            "isMaterializable": True,
            "isPartitioned": False,
            "dependencyKeys": [],
            "assetMaterializations": events,
            "metadataEntries": [
                {"label": "target_table", "text": "raw.events"},
                {
                    "label": "schema",
                    "schema": {
                        "columns": [{"name": location, "type": "BIGINT", "description": None}]
                    },
                },
            ],
        }
        for location in ("iot_prod", "iot_staging")
    ]

    async def graphql(query, variables=None):
        assert "V1Assets" in query, "Read paths must not use the global asset resolver"
        return _asset_inventory_response(nodes)

    calls = []

    async def preview(sql, *, catalog, **kwargs):
        calls.append((sql, catalog))
        return {
            "columns": [{"name": "id", "type": "bigint"}],
            "rows": [
                {"id": value}
                for value in catalogs["dev"]
                .load_table("raw.events")
                .scan()
                .to_arrow()["id"]
                .to_pylist()
            ],
            "has_more": False,
        }

    monkeypatch.setattr(v1_assets, "_graphql", graphql)
    monkeypatch.setattr(v1_assets, "execute_preview", preview)
    detail = http.get("/api/v1/assets/events?env=staging").json()
    assert detail["description"] == "iot_staging"
    assert detail["columns"][0]["name"] == "iot_staging"
    assert detail["last_run_id"] == "iot_staging"  # Not the newer global prod event.
    assert detail["history_scoped"] is True
    preview_response = http.get("/api/v1/assets/events/preview?env=staging")
    assert preview_response.status_code == 200, preview_response.text
    assert sorted(row["id"] for row in preview_response.json()["rows"]) == [7, 11]
    assert calls == [('SELECT * FROM "staging_catalog"."raw"."events" LIMIT 21', "staging_catalog")]
    schema = http.get("/api/v1/tables/raw.events/schema-history?env=staging")
    assert schema.status_code == 200, schema.text
    assert schema.json()["nessie_ref"] == "dev"
    history = http.get("/api/v1/tables/raw.events/snapshots?env=staging").json()
    assert history["current_snapshot_id"] == snapshots["candidate"][1]
    assert history["nessie_ref"] == "dev"
    rolled_back = http.post(
        "/api/v1/tables/raw.events/rollback?env=staging", json={**payload, "nessie_ref": "dev"}
    )
    assert rolled_back.status_code == 200, rolled_back.text
    assert catalogs["dev"].load_table("raw.events").scan().to_arrow()["id"].to_pylist() == [7]
    assert sorted(
        catalogs["main"].load_table("raw.events").scan().to_arrow()["id"].to_pylist()
    ) == [91, 92]
    nodes[1]["assetMaterializations"] = events[:1]
    unobserved = http.get("/api/v1/assets/events?env=staging").json()
    assert unobserved["history_scoped"] is False
    assert unobserved["last_run_id"] is None
    assert unobserved["last_materialization_at"] is None
    assert http.get("/api/v1/assets/events/preview?env=staging").status_code == 200
