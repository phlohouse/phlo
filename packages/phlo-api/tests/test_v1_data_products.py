"""Draft products keep membership, governance and access separate under CAS writes."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

from starlette.requests import Request
from phlo_api.errors import ConflictError

import pytest
from fastapi.testclient import TestClient

from phlo.dataset_projection import build_dataset_authority
from phlo.governance.surface import GovernanceSurface
from phlo_api.api import v1_data_products as products
from phlo_api.main import app
from security_test_support import authenticated_client


@pytest.fixture
def setup_products(monkeypatch, tmp_path):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        '{"sandbox":{"dagster_location":"sandbox","nessie_ref":"sandbox"},"prod":{"dagster_location":"prod","nessie_ref":"main"}}',
    )
    authority = build_dataset_authority(str(tmp_path), surface=GovernanceSurface(tables={}))
    monkeypatch.setattr(products, "_authority", lambda: authority)

    async def assets(request, env):
        products._target(request, env)
        return [
            SimpleNamespace(id="gold/batch_release_metrics", relation="gold.batch_release_metrics"),
            SimpleNamespace(id="silver/qc_results", relation="silver.qc_results"),
        ]

    monkeypatch.setattr(products, "_assets", assets)
    return {
        "product_id": "batch-record-cgt04",
        "name": "Batch record CGT-04",
        "purpose": "Synthetic fixture batch release evidence.",
        "owner": "QC Analytics",
        "classifications": ["internal"],
        "member_dataset_ids": ["gold.batch_release_metrics", "silver.qc_results"],
        "expected_version": 0,
    }


def test_draft_creation_resolves_canonical_members_without_publishing(setup_products):
    response = authenticated_client("operator").post(
        "/api/v1/data-products?env=sandbox", json=setup_products
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["status"] == "draft" and data["version"] == 1
    assert len(data["members"]) == 2
    assert all(member["canonical"]["publication_state"] is None for member in data["members"])
    assert all(member["canonical"]["declared"] is False for member in data["members"])
    assert all(member["canonical"]["readiness"]["ready"] is False for member in data["members"])
    assert data["sharing_status"] == "unavailable"
    assert (
        authenticated_client("viewer").get("/api/v1/data-products?env=prod").json()["items"] == []
    )
    assert (
        authenticated_client("viewer")
        .get("/api/v1/data-products/batch-record-cgt04?env=prod")
        .status_code
        == 404
    )
    assert (
        authenticated_client("viewer")
        .get("/api/v1/data-products/batch-record-cgt04?env=sandbox")
        .json()["version"]
        == 1
    )


def test_invalid_members_and_duplicate_ids_never_write(setup_products):
    client = authenticated_client("operator")
    for members in [["gold.missing"], ["silver.qc_results", "silver.qc_results"]]:
        assert (
            client.post(
                "/api/v1/data-products?env=sandbox",
                json={**setup_products, "member_dataset_ids": members},
            ).status_code
            == 422
        )
    assert client.get("/api/v1/data-products?env=sandbox").json()["items"] == []
    assert client.post("/api/v1/data-products?env=sandbox", json=setup_products).status_code == 201
    assert client.post("/api/v1/data-products?env=sandbox", json=setup_products).status_code == 409


def test_updates_require_current_version_and_keep_original_creation(setup_products):
    client = authenticated_client("operator")
    original = client.post("/api/v1/data-products?env=sandbox", json=setup_products).json()
    payload = {**setup_products, "name": "Revised draft", "expected_version": 1}
    response = client.put("/api/v1/data-products/batch-record-cgt04?env=sandbox", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    assert response.json()["created_at"] == original["created_at"]
    assert (
        client.put("/api/v1/data-products/batch-record-cgt04?env=sandbox", json=payload).status_code
        == 409
    )
    assert (
        client.put(
            "/api/v1/data-products/another?env=sandbox", json={**payload, "expected_version": 2}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("action", ["update", "archive", "restore"])
def test_atomic_cas_rejects_second_writer(setup_products, monkeypatch, action):
    client = authenticated_client("operator")
    assert client.post("/api/v1/data-products?env=sandbox", json=setup_products).status_code == 201
    expected_version = 1
    if action == "restore":
        assert (
            client.post(
                "/api/v1/data-products/batch-record-cgt04/archive?env=sandbox",
                json={"expected_version": 1},
            ).status_code
            == 200
        )
        expected_version = 2

    async def visible(*args):
        return set(setup_products["member_dataset_ids"])

    monkeypatch.setattr(products, "_visible_relations", visible)
    monkeypatch.setattr(
        products,
        "get_request_principal",
        lambda request: SimpleNamespace(
            subject="test:operator", claims={"scopes": ["project:write"]}
        ),
    )
    barrier = Barrier(2)

    def save(name):
        barrier.wait()
        request = Request({"type": "http", "query_string": b"env=sandbox", "headers": []})
        payload = products.ProductDeclaration.model_validate(
            {**setup_products, "expected_version": expected_version, "name": name}
        )
        try:
            if action == "update":
                asyncio.run(
                    products.v1_data_product_update(request, payload.product_id, payload, "sandbox")
                )
            else:
                endpoint = (
                    products.v1_data_product_archive
                    if action == "archive"
                    else products.v1_data_product_restore
                )
                asyncio.run(
                    endpoint(
                        request,
                        payload.product_id,
                        products.ProductLifecycleWrite(expected_version=expected_version),
                        "sandbox",
                    )
                )
        except ConflictError:
            return 409
        return 200

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(save, ["Writer one", "Writer two"]))
    assert sorted(outcomes) == [200, 409]
    assert products._records("sandbox")[0].version == expected_version + 1


def test_readers_and_anonymous_cannot_create_products(setup_products):
    assert (
        authenticated_client("viewer")
        .post("/api/v1/data-products?env=sandbox", json=setup_products)
        .status_code
        == 403
    )
    assert TestClient(app).get("/api/v1/data-products?env=sandbox").status_code == 401
    assert (
        authenticated_client("operator")
        .post("/api/v1/data-products?env=missing", json=setup_products)
        .status_code
        == 422
    )


def test_removed_or_unauthorized_members_do_not_expose_canonical_metadata(
    setup_products, monkeypatch
):
    from fastapi import HTTPException

    client = authenticated_client("operator")
    assert client.post("/api/v1/data-products?env=sandbox", json=setup_products).status_code == 201
    real_enforce = products.enforce_http_operation

    async def deny_member(request, spec, path_params):
        if path_params.get("asset_id") == "silver/qc_results":
            raise HTTPException(403, "Denied")
        return await real_enforce(request, spec, path_params)

    monkeypatch.setattr(products, "enforce_http_operation", deny_member)
    result = (
        authenticated_client("viewer")
        .get("/api/v1/data-products/batch-record-cgt04?env=sandbox")
        .json()
    )
    assert result["members"][0]["canonical"] is not None
    assert result["members"][1]["canonical"] is None
    assert "not authorized" in result["members"][1]["reason"]
    inventory = authenticated_client("viewer").get("/api/v1/data-products?env=sandbox").json()
    assert inventory["items"][0]["members"][1]["canonical"] is None

    async def no_assets(*args, **kwargs):
        return []

    monkeypatch.setattr(products, "_assets", no_assets)
    removed = client.get("/api/v1/data-products/batch-record-cgt04?env=sandbox").json()
    assert all(member["canonical"] is None for member in removed["members"])


def test_draft_mutation_audit_records_actor_and_version(setup_products, tmp_path):
    import json

    client = authenticated_client("operator")
    assert client.post("/api/v1/data-products?env=sandbox", json=setup_products).status_code == 201
    records = [
        json.loads(line)
        for line in (tmp_path / ".phlo/audit/operations.jsonl").read_text().splitlines()
    ]
    record = next(record for record in records if record["operation"] == "data_product.declare")
    assert record["subject"] == "test:operator"
    assert record["target"] == "sandbox/batch-record-cgt04"
    assert record["result"] == {"version": 1, "status": "draft"}


def test_product_collection_and_membership_are_bounded(setup_products):
    client = authenticated_client("operator")
    original = client.post("/api/v1/data-products?env=sandbox", json=setup_products).json()
    record = {
        key: value
        for key, value in original.items()
        if key not in {"members", "sharing_status", "sharing_reason"}
    }
    records = [{**record, "product_id": f"product-{index}"} for index in range(500)]
    products.mutate_collection(
        products.project_root(),
        products._COLLECTION,
        products._legacy_path(),
        lambda items: records,
    )
    response = client.post("/api/v1/data-products?env=sandbox", json=setup_products)
    assert response.status_code == 422
    assert len(products._records("sandbox")) == 500
    assert (
        client.post(
            "/api/v1/data-products?env=sandbox",
            json={**setup_products, "member_dataset_ids": [f"gold.table_{i}" for i in range(51)]},
        ).status_code
        == 422
    )

    base = "/api/v1/data-products"
    url = f"{base}/product-0"
    archived = client.post(f"{url}/archive?env=sandbox", json={"expected_version": 1})
    assert archived.status_code == 200, archived.text
    assert archived.json()["version"] == 2 and archived.json()["status"] == "archived"
    assert len(client.get(f"{base}?env=sandbox").json()["items"]) == 499
    assert len(client.get(f"{base}?env=sandbox&include_archived=true").json()["items"]) == 500
    assert client.post(f"{base}?env=sandbox", json=setup_products).status_code == 201
    assert len(client.get(f"{base}?env=sandbox&include_archived=true").json()["items"]) == 501
    assert (
        client.post(f"{url}/restore?env=sandbox", json={"expected_version": 2}).status_code == 409
    )
    assert (
        client.post(
            f"{base}/product-1/archive?env=sandbox", json={"expected_version": 1}
        ).status_code
        == 200
    )
    restored = client.post(f"{url}/restore?env=sandbox", json={"expected_version": 2})
    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "draft" and restored.json()["version"] == 3

    products.mutate_collection(
        products.project_root(),
        products._COLLECTION,
        products._legacy_path(),
        lambda items: (
            items
            + [{**record, "product_id": f"archived-{i}", "status": "archived"} for i in range(498)]
        ),
    )
    assert (
        client.post(f"{url}/archive?env=sandbox", json={"expected_version": 3}).status_code == 200
    )
    assert (
        client.post(
            f"{base}?env=sandbox", json={**setup_products, "product_id": "last-slot"}
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"{base}/last-slot/archive?env=sandbox", json={"expected_version": 1}
        ).status_code
        == 200
    )
    for index in range(3):
        rejected = client.post(
            f"{base}?env=sandbox", json={**setup_products, "product_id": f"overflow-{index}"}
        )
        assert rejected.status_code == 422 and "retained" in rejected.text
    retained = client.get(f"{base}?env=sandbox&include_archived=true")
    assert retained.status_code == 200 and len(retained.json()["items"]) == 1000
    assert client.get(f"{url}/export?env=sandbox").json()["version"] == 4
    assert (
        client.post(f"{url}/restore?env=sandbox", json={"expected_version": 4}).status_code == 200
    )


def test_product_lifecycle_retains_definition_and_export_without_publication(
    setup_products, monkeypatch, tmp_path
):
    import json

    client = authenticated_client("operator")
    base = "/api/v1/data-products"
    url = f"{base}/batch-record-cgt04"
    original = client.post(f"{base}?env=sandbox", json=setup_products).json()

    async def no_assets(*args, **kwargs):
        return []

    monkeypatch.setattr(products, "_assets", no_assets)
    archived = client.post(f"{url}/archive?env=sandbox", json={"expected_version": 1})
    assert archived.status_code == 200, archived.text
    assert archived.json()["created_at"] == original["created_at"]
    assert archived.json()["status"] == "archived" and archived.json()["version"] == 2
    assert all(member["canonical"] is None for member in archived.json()["members"])
    assert client.get(f"{base}?env=sandbox").json()["items"] == []
    assert (
        client.get(f"{base}?env=sandbox&include_archived=true").json()["items"][0]["status"]
        == "archived"
    )
    assert client.get(f"{url}?env=sandbox").json()["sharing_status"] == "unavailable"
    assert (
        client.put(f"{url}?env=sandbox", json={**setup_products, "expected_version": 2}).status_code
        == 409
    )
    assert (
        client.post(f"{url}/archive?env=sandbox", json={"expected_version": 1}).status_code == 409
    )
    assert (
        client.post(f"{url}/archive?env=sandbox", json={"expected_version": 2}).status_code == 409
    )
    exported = authenticated_client("viewer").get(f"{url}/export?env=sandbox")
    assert exported.status_code == 200, exported.text
    assert exported.json() == {
        "format": "phlo.product-draft",
        "schema_version": 1,
        "env": "sandbox",
        "version": 2,
        "status": "archived",
        "definition": {
            key: value for key, value in setup_products.items() if key != "expected_version"
        },
    }
    # No live source is needed to restore retained metadata. Members remain unavailable.
    restored = client.post(f"{url}/restore?env=sandbox", json={"expected_version": 2})
    assert restored.status_code == 200, restored.text
    assert restored.json()["version"] == 3 and restored.json()["status"] == "draft"
    assert restored.json()["sharing_status"] == "unavailable"
    records = [
        json.loads(line)
        for line in (tmp_path / ".phlo/audit/operations.jsonl").read_text().splitlines()
    ]
    evidence = [
        r for r in records if r["operation"] in {"data_product.archive", "data_product.restore"}
    ]
    assert [r["result"] for r in evidence] == [
        {"version": 2, "status": "archived"},
        {"version": 3, "status": "draft"},
    ]
    assert all(r["subject"] == "test:operator" for r in evidence)


def test_product_lifecycle_permissions_cas_and_environment(setup_products):
    client = authenticated_client("operator")
    base = "/api/v1/data-products"
    url = f"{base}/batch-record-cgt04"
    assert client.post(f"{base}?env=sandbox", json=setup_products).status_code == 201
    for action in ("archive", "restore"):
        assert (
            authenticated_client("viewer")
            .post(f"{url}/{action}?env=sandbox", json={"expected_version": 1})
            .status_code
            == 403
        )
        assert (
            client.post(f"{url}/{action}?env=prod", json={"expected_version": 1}).status_code == 404
        )
    assert TestClient(app).get(f"{url}/export?env=sandbox").status_code == 401
    assert client.get(f"{url}/export?env=prod").status_code == 404
    assert (
        client.post(f"{url}/archive?env=sandbox", json={"expected_version": 0}).status_code == 422
    )
    assert (
        client.post(f"{url}/archive?env=sandbox", json={"expected_version": 1}).status_code == 200
    )
    assert (
        client.put(f"{url}?env=sandbox", json={**setup_products, "expected_version": 2}).status_code
        == 409
    )
    assert (
        client.post(f"{url}/restore?env=sandbox", json={"expected_version": 1}).status_code == 409
    )
    assert client.get(f"{url}?env=sandbox").json()["version"] == 2
