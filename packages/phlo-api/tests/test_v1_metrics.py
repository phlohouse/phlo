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
        metrics._target(request, env)
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
    detail = client.get("/api/v1/metric-models/batch_release?env=sandbox")
    assert detail.status_code == 200
    assert detail.json()["source_status"] == "unavailable"
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


def test_metric_archive_restore_capacity_export_and_evidence(metric_setup, tmp_path):
    client = authenticated_client("operator")
    base = "/api/v1/metric-models"
    url = f"{base}/batch_release"
    original = client.post(f"{base}?env=sandbox", json=metric_setup).json()
    metrics.mutate_collection(
        metrics.project_root(),
        metrics._COLLECTION,
        metrics._path(),
        lambda items: [original] + [{**original, "model_id": f"filler-{i}"} for i in range(499)],
    )
    archived = client.post(f"{url}/archive?env=sandbox", json={"expected_version": 1})
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "archived" and archived.json()["version"] == 2
    assert archived.json()["created_at"] == original["created_at"]
    assert len(client.get(f"{base}?env=sandbox").json()["items"]) == 499
    assert len(client.get(f"{base}?env=sandbox&include_archived=true").json()["items"]) == 500
    assert client.get(f"{url}?env=sandbox").json()["status"] == "archived"
    assert (
        client.post(f"{url}/archive?env=sandbox", json={"expected_version": 1}).status_code == 409
    )
    assert client.post(f"{url}/preview-sql?env=sandbox", json={}).status_code == 409
    assert (
        client.put(f"{url}?env=sandbox", json={**metric_setup, "expected_version": 2}).status_code
        == 409
    )
    exported = client.get(f"{url}/export?env=sandbox")
    assert exported.status_code == 200, exported.text
    assert exported.json() == {
        "format": "phlo.metric-draft",
        "schema_version": 1,
        "env": "sandbox",
        "nessie_ref": "sandbox",
        "version": 2,
        "status": "archived",
        "definition": {
            key: value for key, value in metric_setup.items() if key != "expected_version"
        },
    }
    assert (
        client.post(
            f"{base}?env=sandbox", json={**metric_setup, "model_id": "new-model"}
        ).status_code
        == 201
    )
    assert len(client.get(f"{base}?env=sandbox&include_archived=true").json()["items"]) == 501
    assert (
        client.post(f"{url}/restore?env=sandbox", json={"expected_version": 2}).status_code == 409
    )
    assert (
        client.post(
            f"{base}/new-model/archive?env=sandbox", json={"expected_version": 1}
        ).status_code
        == 200
    )
    restored = client.post(f"{url}/restore?env=sandbox", json={"expected_version": 2})
    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "draft" and restored.json()["version"] == 3
    assert client.post(f"{url}/preview-sql?env=sandbox", json={}).status_code == 200
    records = [
        json.loads(line)
        for line in (tmp_path / ".phlo/audit/operations.jsonl").read_text().splitlines()
    ]
    evidence = [
        r for r in records if r["operation"] in {"metric_model.archive", "metric_model.restore"}
    ]
    assert [r["result"]["version"] for r in evidence] == [2, 2, 3]
    assert all(r["subject"] == "test:operator" for r in evidence)

    # Retaining archives does not reclaim physical capacity. Reach the real boundary.
    metrics.mutate_collection(
        metrics.project_root(),
        metrics._COLLECTION,
        metrics._path(),
        lambda items: (
            items
            + [{**original, "model_id": f"archived-{i}", "status": "archived"} for i in range(498)]
        ),
    )
    assert (
        client.post(f"{url}/archive?env=sandbox", json={"expected_version": 3}).status_code == 200
    )
    last = {**metric_setup, "model_id": "last-slot"}
    assert client.post(f"{base}?env=sandbox", json=last).status_code == 201
    assert (
        client.post(
            f"{base}/last-slot/archive?env=sandbox", json={"expected_version": 1}
        ).status_code
        == 200
    )
    for index in range(3):
        rejected = client.post(
            f"{base}?env=sandbox", json={**metric_setup, "model_id": f"overflow-{index}"}
        )
        assert rejected.status_code == 409 and "retained" in rejected.text
    retained = client.get(f"{base}?env=sandbox&include_archived=true")
    assert retained.status_code == 200 and len(retained.json()["items"]) == 1000
    assert client.get(f"{url}/export?env=sandbox").json()["version"] == 4
    # Restore does not add a retained record and remains possible at the total ceiling.
    assert (
        client.post(f"{url}/restore?env=sandbox", json={"expected_version": 4}).status_code == 200
    )
    assert (
        client.post(f"{url}/archive?env=sandbox", json={"expected_version": 5}).status_code == 200
    )
    receipt = client.post(
        f"{url}/reclaim?env=sandbox",
        json={"expected_version": 6, "confirmed": True, "reason": "Exported for handoff"},
    )
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["version"] == 7 and receipt.json()["status"] == "reclaimed"
    assert (
        client.post(
            f"{base}?env=sandbox", json={**metric_setup, "model_id": "replacement"}
        ).status_code
        == 201
    )
    assert len(client.get(f"{base}?env=sandbox&include_archived=true").json()["items"]) == 1000
    assert client.post(f"{base}?env=sandbox", json=metric_setup).status_code == 409
    gone = client.get(f"{url}?env=sandbox")
    assert gone.status_code == 410 and "permanently reserved" in gone.json()["error"]["message"]
    assert client.get(f"{url}/export?env=sandbox").status_code == 410
    assert (
        client.post(f"{url}/restore?env=sandbox", json={"expected_version": 7}).status_code == 410
    )


@pytest.mark.parametrize("stale", ["missing", "schema", "relation"])
def test_stale_metric_can_be_read_repaired_and_exported(metric_setup, monkeypatch, stale):
    client = authenticated_client("operator")
    base = "/api/v1/metric-models"
    url = f"{base}/batch_release"
    assert client.post(f"{base}?env=sandbox", json=metric_setup).status_code == 201
    original_source = metrics.v1_asset_detail

    async def source(request, asset_id, env):
        result = await original_source(request, "gold/batch_release_metrics", env)
        if asset_id == metric_setup["asset_id"]:
            if stale == "missing":
                raise NotFoundError("Removed")
            if stale == "schema":
                result.columns = []
            else:
                result.relation = "gold.renamed"
        else:
            result.relation = "gold.replacement"
        return result

    monkeypatch.setattr(metrics, "v1_asset_detail", source)
    detail = client.get(f"{url}?env=sandbox")
    assert detail.status_code == 200 and detail.json()["source_status"] == "unavailable"
    assert client.get(f"{url}/export?env=sandbox").status_code == 200
    assert (
        client.post(f"{url}/archive?env=sandbox", json={"expected_version": 1}).status_code == 200
    )
    assert (
        client.put(f"{url}?env=sandbox", json={**metric_setup, "expected_version": 2}).status_code
        == 409
    )
    assert (
        client.post(f"{url}/restore?env=sandbox", json={"expected_version": 2}).status_code == 200
    )
    payload = {**metric_setup, "asset_id": "gold/replacement", "expected_version": 3}
    repaired = client.put(f"{url}?env=sandbox", json=payload)
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["relation"] == "gold.replacement" and repaired.json()["version"] == 4
    assert client.get(f"{url}?env=sandbox").json()["source_status"] == "available"
    assert (
        '"gold"."replacement"'
        in client.post(f"{url}/preview-sql?env=sandbox", json={}).json()["sql"]
    )
    assert client.put(f"{url}?env=sandbox", json=payload).status_code == 409


@pytest.mark.parametrize("operation", ["update", "archive", "reclaim"])
@pytest.mark.parametrize("expected_version", [1, 2])
def test_metric_mutation_authorizes_the_exact_version(
    metric_setup, monkeypatch, operation, expected_version
):
    client = authenticated_client("operator")
    base = "/api/v1/metric-models"
    url = f"{base}/batch_release"
    assert client.post(f"{base}?env=sandbox", json=metric_setup).status_code == 201
    base_version = 1
    if operation == "reclaim":
        assert (
            client.post(f"{url}/archive?env=sandbox", json={"expected_version": 1}).status_code
            == 200
        )
        base_version = 2
        expected_version += 1
    original_enforce = metrics.enforce_http_operation
    advanced = False

    async def advance_during_authorization(request, spec, params):
        nonlocal advanced
        if params.get("asset_id") == "gold/forbidden":
            raise HTTPException(403, "Denied")
        if params.get("asset_id") == metric_setup["asset_id"] and not advanced:
            advanced = True
            metrics.mutate_collection(
                metrics.project_root(),
                metrics._COLLECTION,
                metrics._path(),
                lambda items: [
                    {**item, "asset_id": "gold/forbidden", "version": base_version + 1}
                    for item in items
                ],
            )
        return await original_enforce(request, spec, params)

    monkeypatch.setattr(metrics, "enforce_http_operation", advance_during_authorization)
    response = (
        client.put(
            f"{url}?env=sandbox", json={**metric_setup, "expected_version": expected_version}
        )
        if operation == "update"
        else client.post(
            f"{url}/{operation}?env=sandbox",
            json={
                "expected_version": expected_version,
                **(
                    {"confirmed": True, "reason": "Retire archived draft"}
                    if operation == "reclaim"
                    else {}
                ),
            },
        )
    )
    assert response.status_code == 409, response.text
    monkeypatch.setattr(metrics, "enforce_http_operation", original_enforce)
    retained = client.get(f"{url}/export?env=sandbox").json()
    assert retained["status"] == ("archived" if operation == "reclaim" else "draft")
    assert retained["version"] == (base_version + 1 if advanced else base_version)
    assert retained["definition"]["asset_id"] == (
        "gold/forbidden" if advanced else metric_setup["asset_id"]
    )


def test_metric_lifecycle_and_repair_preserve_permissions_and_ref(metric_setup, monkeypatch):
    client = authenticated_client("operator")
    base = "/api/v1/metric-models"
    url = f"{base}/batch_release"
    assert client.post(f"{base}?env=sandbox", json=metric_setup).status_code == 201
    assert (
        authenticated_client("viewer")
        .post(f"{url}/archive?env=sandbox", json={"expected_version": 1})
        .status_code
        == 403
    )
    assert TestClient(app).get(f"{url}/export?env=sandbox").status_code == 401
    assert client.get(f"{url}/export?env=prod").status_code == 404
    original_enforce = metrics.enforce_http_operation
    denied_asset = metric_setup["asset_id"]

    async def deny_original(request, spec, params):
        if params.get("asset_id") == denied_asset:
            raise HTTPException(403, "Denied")
        return await original_enforce(request, spec, params)

    monkeypatch.setattr(metrics, "enforce_http_operation", deny_original)
    assert client.get(f"{url}/export?env=sandbox").status_code == 403
    assert (
        client.post(f"{url}/archive?env=sandbox", json={"expected_version": 1}).status_code == 403
    )
    assert (
        client.put(
            f"{url}?env=sandbox",
            json={**metric_setup, "asset_id": "gold/replacement", "expected_version": 1},
        ).status_code
        == 403
    )
    denied_asset = "gold/replacement"
    assert (
        client.put(
            f"{url}?env=sandbox",
            json={**metric_setup, "asset_id": "gold/replacement", "expected_version": 1},
        ).status_code
        == 403
    )
    monkeypatch.setattr(metrics, "enforce_http_operation", original_enforce)
    assert client.get(f"{url}?env=sandbox").json()["version"] == 1
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS", '{"sandbox":{"dagster_location":"sandbox","nessie_ref":"other"}}'
    )
    assert client.get(f"{url}/export?env=sandbox").status_code == 404
    assert (
        client.post(f"{url}/archive?env=sandbox", json={"expected_version": 1}).status_code == 404
    )


def test_metric_reclaim_requires_confirmation_version_and_asset_authorization(
    metric_setup, monkeypatch, tmp_path
):
    from hashlib import sha256

    client = authenticated_client("operator")
    base = "/api/v1/metric-models"
    url = f"{base}/batch_release"
    payload = {"expected_version": 2, "confirmed": True, "reason": "  Exported for review  "}
    assert client.post(f"{base}?env=sandbox", json=metric_setup).status_code == 201
    assert (
        client.post(
            f"{url}/reclaim?env=sandbox", json={**payload, "expected_version": 1}
        ).status_code
        == 409
    )
    archived = client.post(f"{url}/archive?env=sandbox", json={"expected_version": 1}).json()
    for change in (
        {"confirmed": False},
        {"reason": "   "},
        {"expected_version": 1},
        {"expected_version": 3},
    ):
        assert client.post(
            f"{url}/reclaim?env=sandbox", json={**payload, **change}
        ).status_code == (422 if "expected_version" not in change else 409)
    assert (
        authenticated_client("viewer").post(f"{url}/reclaim?env=sandbox", json=payload).status_code
        == 403
    )
    assert TestClient(app).post(f"{url}/reclaim?env=sandbox", json=payload).status_code == 401
    assert client.post(f"{url}/reclaim?env=prod", json=payload).status_code == 404
    original_enforce = metrics.enforce_http_operation

    async def denied(*args, **kwargs):
        raise HTTPException(403, "Denied")

    monkeypatch.setattr(metrics, "enforce_http_operation", denied)
    assert client.post(f"{url}/reclaim?env=sandbox", json=payload).status_code == 403
    monkeypatch.setattr(metrics, "enforce_http_operation", original_enforce)

    async def missing_source(*args, **kwargs):
        raise NotFoundError("Removed source")

    with monkeypatch.context() as stale_source:
        stale_source.setattr(metrics, "v1_asset_detail", missing_source)
        reclaimed = client.post(f"{url}/reclaim?env=sandbox", json=payload)
    assert reclaimed.status_code == 200, reclaimed.text
    receipt = reclaimed.json()
    assert (
        receipt["definition_sha256"]
        == sha256(json.dumps(archived, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    )
    assert receipt["reclaimed_by"] == "test:operator" and receipt["reason"] == "Exported for review"
    assert receipt["created_at"] == archived["created_at"] and receipt["version"] == 3
    stored = metrics.load_collection(metrics.project_root(), metrics._COLLECTION, metrics._path())
    assert stored == [receipt] and "measures" not in receipt
    assert client.post(f"{url}/preview-sql?env=sandbox", json={}).status_code == 410
    assert (
        client.put(f"{url}?env=sandbox", json={**metric_setup, "expected_version": 3}).status_code
        == 410
    )
    assert client.get(f"{base}?env=sandbox&include_archived=true").json()["items"] == []
    # Unrelated writes must carry the durable identity fence forward unchanged.
    assert (
        client.post(f"{base}?env=sandbox", json={**metric_setup, "model_id": "another"}).status_code
        == 201
    )
    assert client.post(f"{base}?env=sandbox", json=metric_setup).status_code == 409
    monkeypatch.setattr(metrics, "enforce_http_operation", denied)
    hidden = client.get(f"{url}?env=sandbox")
    assert hidden.status_code == 403 and receipt["definition_sha256"] not in hidden.text
    monkeypatch.setattr(metrics, "enforce_http_operation", original_enforce)
    audits = [
        json.loads(line)
        for line in (tmp_path / ".phlo/audit/operations.jsonl").read_text().splitlines()
    ]
    assert (
        next(item for item in audits if item["operation"] == "metric_model.reclaim")["result"]
        == receipt
    )


@pytest.mark.parametrize("fence", ["record", "collection", "legacy", "racing_collection"])
def test_metric_reclaim_preserves_unknown_mutation_evidence(
    metric_setup, tmp_path, fence, monkeypatch
):
    from phlo.plugins.observatory_settings import SettingsScope, get_settings_service
    from phlo_api.observatory_api.observatory_durable_state import state_namespace

    client = authenticated_client("operator")
    base = "/api/v1/metric-models"
    url = f"{base}/batch_release"
    service = get_settings_service()
    namespace = state_namespace(tmp_path, metrics._COLLECTION)
    if fence == "legacy":
        metrics._path().parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(
            {"items": [], "unknown_mutation": {"state": "unknown", "key": "do-not-retry"}}
        )
        metrics._path().write_text(raw)
    else:
        assert client.post(f"{base}?env=sandbox", json=metric_setup).status_code == 201
        assert (
            client.post(f"{url}/archive?env=sandbox", json={"expected_version": 1}).status_code
            == 200
        )

        def add_fence(current):
            if fence == "record":
                current["items"][0]["unknown_mutation"] = {"state": "unknown"}
            else:
                current["unknown_mutation"] = {"state": "unknown"}
            return current

        if fence == "racing_collection":
            original_enforce = metrics.enforce_http_operation

            async def racing_enforce(*args, **kwargs):
                await original_enforce(*args, **kwargs)
                service.mutate(SettingsScope.GLOBAL, namespace, add_fence)

            monkeypatch.setattr(metrics, "enforce_http_operation", racing_enforce)
            state = service.get(SettingsScope.GLOBAL, namespace).settings
            before = json.dumps(add_fence(state), sort_keys=True)
        else:
            service.mutate(SettingsScope.GLOBAL, namespace, add_fence)
            before = json.dumps(
                service.get(SettingsScope.GLOBAL, namespace).settings, sort_keys=True
            )
    response = client.post(
        f"{url}/reclaim?env=sandbox",
        json={"expected_version": 2, "confirmed": True, "reason": "Retire"},
    )
    assert response.status_code == 409, response.text
    if fence == "legacy":
        assert metrics._path().read_text() == raw
        assert service.get(SettingsScope.GLOBAL, namespace) is None
    else:
        assert (
            json.dumps(service.get(SettingsScope.GLOBAL, namespace).settings, sort_keys=True)
            == before
        )


def test_metric_reclaim_retains_recent_content_and_capacity_under_audit_policy(
    metric_setup, tmp_path
):
    from phlo.plugins.observatory_settings import (
        ADMIN_SETTINGS_NAMESPACE,
        SettingsScope,
        get_settings_service,
    )

    client = authenticated_client("operator")
    base = "/api/v1/metric-models"
    url = f"{base}/batch_release"
    assert client.post(f"{base}?env=sandbox", json=metric_setup).status_code == 201
    archived = client.post(f"{url}/archive?env=sandbox", json={"expected_version": 1}).json()
    old_creation = {**archived, "created_at": "1990-01-01T00:00:00+00:00"}
    metrics.mutate_collection(
        tmp_path,
        metrics._COLLECTION,
        metrics._path(),
        lambda items: (
            [old_creation] + [{**old_creation, "model_id": f"retained-{i}"} for i in range(999)]
        ),
    )
    get_settings_service().put(
        SettingsScope.GLOBAL,
        ADMIN_SETTINGS_NAMESPACE,
        {"version": 1, "values": {"observatory.settings.audit.retention_years": 7}},
    )
    before = metrics.load_collection(tmp_path, metrics._COLLECTION, metrics._path())
    audit_path = tmp_path / ".phlo/audit/operations.jsonl"
    audit_before = audit_path.read_bytes()
    payload = {"expected_version": 2, "confirmed": True, "reason": "Exported for handoff"}
    denied = client.post(f"{url}/reclaim?env=sandbox", json=payload)
    assert denied.status_code == 409 and "retention_minimum_not_elapsed" in denied.text
    assert metrics.load_collection(tmp_path, metrics._COLLECTION, metrics._path()) == before
    assert audit_path.read_bytes() == audit_before
    assert client.get(f"{url}/export?env=sandbox").json()["version"] == 2
    replacement = {**metric_setup, "model_id": "replacement"}
    assert client.post(f"{base}?env=sandbox", json=replacement).status_code == 409
    # Existing content becomes eligible only after its latest update passes the minimum.
    metrics.mutate_collection(
        tmp_path,
        metrics._COLLECTION,
        metrics._path(),
        lambda items: [
            {**item, "updated_at": "1991-01-01T00:00:00+00:00"}
            if item["model_id"] == "batch_release"
            else item
            for item in items
        ],
    )
    receipt = client.post(f"{url}/reclaim?env=sandbox", json=payload)
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["version"] == 3
    assert client.post(f"{base}?env=sandbox", json=replacement).status_code == 201
    assert receipt.json() in metrics.load_collection(tmp_path, metrics._COLLECTION, metrics._path())
    evidence = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert (
        next(item for item in evidence if item["operation"] == "metric_model.reclaim")["result"]
        == receipt.json()
    )
