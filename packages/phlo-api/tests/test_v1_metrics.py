"""Metric drafts validate live source types and retain environment/ref/CAS boundaries."""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from phlo_api.api import v1_metrics as metrics
from phlo_api.errors import NotFoundError
from phlo_api.main import app
from security_test_support import authenticated_client


@pytest.fixture
def metric_setup(monkeypatch, tmp_path):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        '{"sandbox":{"dagster_location":"sandbox","nessie_ref":"sandbox"},"prod":{"dagster_location":"prod","nessie_ref":"main"}}',
    )

    async def source(request, asset_id, env):
        if asset_id != "gold/batch_release_metrics":
            raise NotFoundError("Source asset missing.")
        return SimpleNamespace(
            relation="gold.batch_release_metrics",
            columns=[
                SimpleNamespace(name="batch_id", type="string"),
                SimpleNamespace(name="potency_mean_pct", type="double"),
                SimpleNamespace(name="release_date", type="date"),
            ],
        )

    monkeypatch.setattr(metrics, "v1_asset_detail", source)
    return {
        "model_id": "batch_release",
        "description": "Synthetic release measurements.",
        "owner": "QC Analytics",
        "asset_id": "gold/batch_release_metrics",
        "dimensions": [
            {"name": "batch", "column": "batch_id", "kind": "string", "description": ""},
            {"name": "released", "column": "release_date", "kind": "time", "description": ""},
        ],
        "measures": [
            {
                "name": "mean_potency",
                "column": "potency_mean_pct",
                "aggregation": "avg",
                "description": "",
            }
        ],
        "time_dimension": "released",
        "expected_version": 0,
    }


def test_metric_create_persist_and_read_bound_to_environment_ref(metric_setup):
    client = authenticated_client("operator")
    created = client.post("/api/v1/metric-models?env=sandbox", json=metric_setup)
    assert created.status_code == 201, created.text
    assert created.json()["version"] == 1 and created.json()["status"] == "draft"
    assert (
        client.get("/api/v1/metric-models?env=sandbox").json()["items"][0]["model_id"]
        == "batch_release"
    )
    assert client.get("/api/v1/metric-models?env=prod").json()["items"] == []
    assert client.get("/api/v1/metric-models/batch_release?env=prod").status_code == 404


def test_metric_cas_and_actual_type_validation(metric_setup):
    client = authenticated_client("operator")
    assert client.post("/api/v1/metric-models?env=sandbox", json=metric_setup).status_code == 201
    payload = {**metric_setup, "expected_version": 1, "owner": "Revised owner"}
    first = client.put("/api/v1/metric-models/batch_release?env=sandbox", json=payload)
    assert first.status_code == 200 and first.json()["version"] == 2
    assert (
        client.put("/api/v1/metric-models/batch_release?env=sandbox", json=payload).status_code
        == 409
    )
    payload["expected_version"] = 2
    payload["dimensions"][0]["kind"] = "number"
    assert (
        client.put("/api/v1/metric-models/batch_release?env=sandbox", json=payload).status_code
        == 422
    )
    assert client.get("/api/v1/metric-models/batch_release?env=sandbox").json()["version"] == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"model_id": "bad;DROP"},
        {"asset_id": "gold/missing"},
        {
            "measures": [
                {
                    "name": "injected",
                    "column": "potency_mean_pct);DELETE",
                    "aggregation": "avg",
                    "description": "",
                }
            ]
        },
        {
            "measures": [
                {"name": "bad", "column": "batch_id", "aggregation": "avg", "description": ""}
            ]
        },
        {
            "measures": [
                {
                    "name": "bad",
                    "column": "potency_mean_pct",
                    "aggregation": "execute",
                    "description": "",
                }
            ]
        },
        {"time_dimension": "batch"},
    ],
)
def test_invalid_metric_definitions_never_persist(metric_setup, changes):
    client = authenticated_client("operator")
    assert client.post(
        "/api/v1/metric-models?env=sandbox", json={**metric_setup, **changes}
    ).status_code in {404, 422}
    assert client.get("/api/v1/metric-models?env=sandbox").json()["items"] == []


def test_metric_preview_compiles_readonly_sql_and_rechecks_current_source(
    metric_setup, monkeypatch
):
    client = authenticated_client("operator")
    assert client.post("/api/v1/metric-models?env=sandbox", json=metric_setup).status_code == 201
    response = client.post(
        "/api/v1/metric-models/batch_release/preview-sql?env=sandbox", json={"group_by": ["batch"]}
    )
    assert response.status_code == 200, response.text
    assert (
        response.json()["sql"]
        == 'SELECT\n  "batch_id" AS "batch",\n  AVG("potency_mean_pct") AS "mean_potency"\nFROM "gold"."batch_release_metrics"\nGROUP BY 1\nORDER BY 1\nLIMIT 100'
    )
    assert (
        client.post(
            "/api/v1/metric-models/batch_release/preview-sql?env=sandbox",
            json={"group_by": ["unknown"]},
        ).status_code
        == 422
    )

    async def removed(*args, **kwargs):
        raise NotFoundError("Source asset missing.")

    monkeypatch.setattr(metrics, "v1_asset_detail", removed)
    inventory = client.get("/api/v1/metric-models?env=sandbox")
    assert inventory.status_code == 200
    assert inventory.json()["items"][0]["source_status"] == "unavailable"
    assert inventory.json()["items"][0]["source_reason"]
    assert client.get("/api/v1/metric-models/batch_release?env=sandbox").status_code == 404
    assert (
        client.post(
            "/api/v1/metric-models/batch_release/preview-sql?env=sandbox", json={"group_by": []}
        ).status_code
        == 404
    )


def test_metric_authentication_and_source_authorization(metric_setup, monkeypatch):
    assert TestClient(app).get("/api/v1/metric-models?env=sandbox").status_code == 401
    assert (
        authenticated_client("viewer")
        .post("/api/v1/metric-models?env=sandbox", json=metric_setup)
        .status_code
        == 403
    )
    client = authenticated_client("operator")
    assert client.post("/api/v1/metric-models?env=sandbox", json=metric_setup).status_code == 201

    async def denied(*args, **kwargs):
        raise HTTPException(403, "Source permission revoked")

    monkeypatch.setattr(metrics, "enforce_http_operation", denied)
    inventory = client.get("/api/v1/metric-models?env=sandbox")
    assert inventory.status_code == 200 and inventory.json()["items"] == []
    assert client.get("/api/v1/metric-models/batch_release?env=sandbox").status_code == 403
    assert (
        client.post(
            "/api/v1/metric-models/batch_release/preview-sql?env=sandbox", json={"group_by": []}
        ).status_code
        == 403
    )

    async def unavailable(*args, **kwargs):
        raise HTTPException(503, "Authorization backend unavailable")

    monkeypatch.setattr(metrics, "enforce_http_operation", unavailable)
    assert client.get("/api/v1/metric-models?env=sandbox").status_code == 503


@pytest.mark.parametrize("stale", ["missing", "schema", "relation"])
def test_metric_inventory_isolates_stale_and_unauthorized_models(metric_setup, monkeypatch, stale):
    original_source = metrics.v1_asset_detail
    changed = False

    async def source(request, asset_id, env):
        result = await original_source(request, "gold/batch_release_metrics", env)
        if changed and asset_id == "stale":
            if stale == "missing":
                raise NotFoundError("Private source error")
            if stale == "schema":
                result.columns = []
            else:
                result.relation = "gold.replaced"
        return result

    monkeypatch.setattr(metrics, "v1_asset_detail", source)
    client = authenticated_client("operator")
    for name in ("healthy", "stale", "hidden"):
        response = client.post(
            "/api/v1/metric-models?env=sandbox",
            json={**metric_setup, "model_id": name, "asset_id": name},
        )
        assert response.status_code == 201, response.text
    changed = True
    original_enforce = metrics.enforce_http_operation

    async def authorize(request, spec, params):
        if params.get("asset_id") == "hidden":
            raise HTTPException(403, "Source permission revoked")
        await original_enforce(request, spec, params)

    monkeypatch.setattr(metrics, "enforce_http_operation", authorize)
    response = client.get("/api/v1/metric-models?env=sandbox")
    assert response.status_code == 200, response.text
    items = {item["model_id"]: item for item in response.json()["items"]}
    assert set(items) == {"healthy", "stale"}
    assert items["healthy"]["source_status"] == "available"
    assert items["healthy"]["source_reason"] is None
    assert items["stale"]["source_status"] == "unavailable"
    assert "Private source error" not in response.text
    assert (
        client.post(
            "/api/v1/metric-models/stale/preview-sql?env=sandbox", json={"group_by": []}
        ).status_code
        == {"missing": 404, "schema": 422, "relation": 409}[stale]
    )


def test_metric_audit_records_actor_and_committed_version(metric_setup, tmp_path):
    assert (
        authenticated_client("operator")
        .post("/api/v1/metric-models?env=sandbox", json=metric_setup)
        .status_code
        == 201
    )
    records = [
        json.loads(line)
        for line in (tmp_path / ".phlo/audit/operations.jsonl").read_text().splitlines()
    ]
    record = next(record for record in records if record["operation"] == "metric_model.create")
    assert record["subject"] == "test:operator"
    assert record["result"] == {"version": 1, "status": "draft"}
