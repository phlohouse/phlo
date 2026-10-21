"""Phlo Transform read models on the v1 asset surface."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from security_test_support import authenticated_client

from phlo_api.api import v1_assets


def _node() -> dict[str, Any]:
    return {
        "assetKey": {"path": ["customer_dimension"]},
        "description": "Type-2 customer dimension",
        "computeKind": "transform",
        "kinds": ["transform", "sql", "table"],
        "groupName": "customer360",
        "isMaterializable": True,
        "isObservable": False,
        "isPartitioned": False,
        "hasAssetChecks": True,
        "tags": [],
        "jobNames": ["customer360_identity_rebuild_job"],
        "dependencyKeys": [{"path": ["stg_commerce_customers"]}],
        "repository": {
            "name": "__repository__",
            "location": {"name": "phlo_dagster.framework.definitions"},
        },
        "metadataEntries": [
            {
                "__typename": "TextMetadataEntry",
                "label": "transform_engine",
                "text": "phlo-transform",
            },
            {
                "__typename": "TextMetadataEntry",
                "label": "transform_model",
                "text": "customer360.customer_dimension",
            },
            {
                "__typename": "TextMetadataEntry",
                "label": "transform_target",
                "text": "customer360.customer_dimension",
            },
            {
                "__typename": "TextMetadataEntry",
                "label": "transform_materialization",
                "text": "table",
            },
            {"__typename": "TextMetadataEntry", "label": "transform_version", "text": "3ab2ce0"},
            {
                "__typename": "TextMetadataEntry",
                "label": "transform_path",
                "text": "transforms/customer_dimension.sql",
            },
            {
                "__typename": "TextMetadataEntry",
                "label": "transform_workspace",
                "text": "/app/project/workflows/transforms/native",
            },
            {"__typename": "JsonMetadataEntry", "label": "transform_tags", "jsonString": "[]"},
            {"__typename": "JsonMetadataEntry", "label": "transform_sources", "jsonString": "[]"},
            {
                "__typename": "JsonMetadataEntry",
                "label": "transform_source_assets",
                "jsonString": '["sling_c360_customers"]',
            },
            {
                "__typename": "JsonMetadataEntry",
                "label": "transform_test_names",
                "jsonString": '["customer360.customer_dimension.generated.not_null_canonical_email"]',
            },
        ],
        "assetMaterializations": [
            {
                "timestamp": 1720000000000,
                "runId": "run-1",
                "partition": None,
                "runOrError": {
                    "__typename": "Run",
                    "runId": "run-1",
                    "status": "SUCCESS",
                    "pipelineName": "customer360_identity_rebuild_job",
                    "tags": [{"key": "phlo/ref", "value": "main"}],
                    "repositoryOrigin": {
                        "repositoryLocationName": "phlo_dagster.framework.definitions"
                    },
                },
                "metadataEntries": [
                    {
                        "__typename": "TextMetadataEntry",
                        "label": "transform_status",
                        "text": "passed",
                    },
                    {
                        "__typename": "TextMetadataEntry",
                        "label": "transform_action",
                        "text": "build",
                    },
                    {
                        "__typename": "JsonMetadataEntry",
                        "label": "transform_reasons",
                        "jsonString": '["SQL changed"]',
                    },
                    {
                        "__typename": "TextMetadataEntry",
                        "label": "transform_previous_version",
                        "text": "3ab2ce0",
                    },
                    {
                        "__typename": "TextMetadataEntry",
                        "label": "transform_desired_version",
                        "text": "45f19bc",
                    },
                    {
                        "__typename": "TextMetadataEntry",
                        "label": "transform_sql_hash",
                        "text": "hash-1",
                    },
                    {
                        "__typename": "TextMetadataEntry",
                        "label": "transform_query_id",
                        "text": "query-1",
                    },
                    {
                        "__typename": "TextMetadataEntry",
                        "label": "transform_run_id",
                        "text": "run-1",
                    },
                    {
                        "__typename": "TextMetadataEntry",
                        "label": "transform_plan_id",
                        "text": "plan-1",
                    },
                    {
                        "__typename": "IntMetadataEntry",
                        "label": "transform_duration_ms",
                        "intValue": 12,
                    },
                    {
                        "__typename": "JsonMetadataEntry",
                        "label": "transform_counts",
                        "jsonString": '{"passed": 2, "failed": 0}',
                    },
                ],
            }
        ],
    }


def test_asset_view_reads_transform_metadata() -> None:
    view = v1_assets._asset_view(copy.deepcopy(_node()), "main")

    assert view.transform is not None
    assert view.transform.engine == "phlo-transform"
    assert view.transform.model == "customer360.customer_dimension"
    assert view.transform.materialization == "table"
    assert view.transform.version == "3ab2ce0"
    assert view.transform.source_assets == ["sling_c360_customers"]
    assert view.transform.tests == [
        "customer360.customer_dimension.generated.not_null_canonical_email"
    ]
    assert view.transform.last_run is not None
    assert view.transform.last_run.status == "passed"
    assert view.transform.last_run.action == "build"
    assert view.transform.last_run.reasons == ["SQL changed"]
    assert view.transform.last_run.previous_version == "3ab2ce0"
    assert view.transform.last_run.desired_version == "45f19bc"
    assert view.transform.last_run.sql_hash == "hash-1"
    assert view.transform.last_run.query_id == "query-1"
    assert view.transform.last_run.run_id == "run-1"
    assert view.transform.last_run.duration_ms == 12
    assert view.transform.last_run.counts == {"passed": 2, "failed": 0}


def test_asset_view_tolerates_missing_run_facts() -> None:
    node = copy.deepcopy(_node())
    entries = node["assetMaterializations"][0]["metadataEntries"]
    entries[:] = [
        entry
        for entry in entries
        if entry["label"] not in {"transform_duration_ms", "transform_counts"}
    ]
    entries.append({"__typename": "NullMetadataEntry", "label": "transform_duration_ms"})

    view = v1_assets._asset_view(node, "main")

    assert view.transform is not None
    assert view.transform.last_run is not None
    assert view.transform.last_run.duration_ms is None
    assert view.transform.last_run.counts == {}


def test_plain_asset_has_no_transform_block() -> None:
    node = copy.deepcopy(_node())
    node["metadataEntries"] = [
        entry for entry in node["metadataEntries"] if not entry["label"].startswith("transform_")
    ]

    view = v1_assets._asset_view(node, "main")

    assert view.transform is None


def _configure_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        json.dumps(
            {
                "dev": {
                    "dagster_location": "phlo_dagster.framework.definitions",
                    "nessie_ref": "main",
                }
            }
        ),
    )


def test_transform_endpoint_aggregates_models(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_asset_nodes(request, env, **kwargs):
        return [copy.deepcopy(_node())]

    _configure_environment(monkeypatch)
    monkeypatch.setattr(v1_assets, "_asset_nodes", fake_asset_nodes)
    response = authenticated_client("admin").get("/api/v1/transform?env=dev")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "available"
    assert payload["engine"] == "phlo-transform"
    assert payload["model_count"] == 1
    assert payload["test_count"] == 1
    assert payload["source_count"] == 0
    assert payload["workspace"] == "/app/project/workflows/transforms/native"
    assert payload["models"][0]["asset_id"] == "customer_dimension"
    assert payload["models"][0]["tests"] == [
        "customer360.customer_dimension.generated.not_null_canonical_email"
    ]


def test_transform_endpoint_reports_the_dbt_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_asset_nodes(request, env, **kwargs):
        node = copy.deepcopy(_node())
        for entry in node["metadataEntries"]:
            if entry["label"] == "transform_engine":
                entry["text"] = "dbt"
        return [node]

    _configure_environment(monkeypatch)
    monkeypatch.setattr(v1_assets, "_asset_nodes", fake_asset_nodes)
    response = authenticated_client("admin").get("/api/v1/transform?env=dev")

    assert response.status_code == 200, response.text
    assert response.json()["engine"] == "dbt"


def test_transform_endpoint_explains_an_inactive_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_asset_nodes(request, env, **kwargs):
        node = copy.deepcopy(_node())
        node["metadataEntries"] = []
        return [node]

    _configure_environment(monkeypatch)
    monkeypatch.setattr(v1_assets, "_asset_nodes", fake_asset_nodes)
    response = authenticated_client("admin").get("/api/v1/transform?env=dev")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "unavailable"
    assert "phlo transform status" in payload["reason"]
    assert payload["models"] == []
