"""Tests for the Dagster operation capability adapter.

Covers the GraphQL request shapes sent to Dagster (asset selection, run
status, partition dimensions), explicit user token usage, error surfacing
from HTTP 500 responses, and write-ahead-publish tag survival across
launch retries without duplicate runs.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from phlo_dagster.operations import (
    get_run_status,
    launch_materialize,
    launch_retry,
    list_partitions,
    terminate,
)


def test_launch_materialize_posts_asset_selection(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_post(self, url, json=None, headers=None):  # noqa: ANN001, ANN202, ARG001
        captured["url"] = url
        captured["json"] = json
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "data": {
                    "launchPipelineExecution": {
                        "__typename": "LaunchRunSuccess",
                        "run": {"runId": "run-1", "status": "STARTED"},
                    }
                }
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    result = asyncio.run(
        launch_materialize(
            dagster_url="http://dagster.test/graphql",
            asset_key_path="silver/orders",
            job_name="orders_job",
            partition_key="2026-05-28",
        )
    )

    assert result.accepted is True
    assert result.run_id == "run-1"
    assert captured["url"] == "http://dagster.test/graphql"
    variables = captured["json"]["variables"]  # type: ignore[index]
    selector = variables["executionParams"]["selector"]
    assert selector["pipelineName"] == "orders_job"
    assert selector["assetSelection"] == [{"path": ["silver", "orders"]}]
    assert "repositoryLocationName" not in selector
    assert "repositoryName" not in selector


def test_get_run_status_reads_dagster_run(monkeypatch) -> None:
    async def fake_post(self, url, json=None, headers=None):  # noqa: ANN001, ANN202, ARG001
        assert "RunStatus" in json["query"]
        assert json["variables"] == {"runId": "run-1"}
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "data": {
                    "runOrError": {
                        "__typename": "Run",
                        "runId": "run-1",
                        "status": "SUCCESS",
                    }
                }
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    assert (
        asyncio.run(get_run_status(dagster_url="http://dagster.test/graphql", run_id="run-1"))
        == "SUCCESS"
    )


def test_launch_materialize_exposes_graphql_errors_from_http_500(monkeypatch) -> None:
    async def fake_post(self, url, json=None, headers=None):  # noqa: ANN001, ANN202, ARG001
        return httpx.Response(
            500,
            request=httpx.Request("POST", url),
            json={"errors": [{"message": "repositoryName is required"}]},
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    with pytest.raises(RuntimeError, match="repositoryName is required"):
        asyncio.run(
            launch_materialize(
                dagster_url="http://dagster.test/graphql",
                asset_key_path="silver/orders",
                job_name="orders_job",
            )
        )


def test_launch_materialize_uses_the_explicit_user_access_token(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_post(self, url, json=None, headers=None):  # noqa: ANN001, ANN202, ARG001
        captured["headers"] = headers
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "data": {
                    "launchPipelineExecution": {
                        "__typename": "LaunchRunSuccess",
                        "run": {"runId": "run-1", "status": "STARTED"},
                    }
                }
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(
        "phlo_dagster.operations.build_service_headers",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not impersonate phlo-api")
        ),
    )

    result = asyncio.run(
        launch_materialize(
            dagster_url="http://dagster.test/graphql",
            asset_key_path="silver/orders",
            job_name="orders_job",
            repository_location_name="phlo_dagster",
            repository_name="phlo_dagster",
            access_token="verified-user-token",
        )
    )

    assert result.accepted is True
    assert captured["headers"] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer verified-user-token",
    }


def test_wap_materialize_tags_survive_retry(monkeypatch) -> None:
    payloads: list[dict[str, object]] = []

    async def fake_post(self, url, json=None, headers=None):  # noqa: ANN001, ANN202, ARG001
        if "ExistingMaterializationRun" in json["query"]:
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"data": {"runsOrError": {"__typename": "Runs", "results": []}}},
            )
        payloads.append(json)
        mutation = "launchPipelineExecution" if len(payloads) == 1 else "launchPipelineReexecution"
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "data": {
                    mutation: {
                        "__typename": "LaunchRunSuccess",
                        "run": {"runId": "dagster-run", "status": "STARTED"},
                    }
                }
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    tags = {
        "phlo/run_id": "request-42",
        "phlo/wap_branch": "pipeline-run-request-42",
        "phlo/project_id": "warehouse",
        "phlo/attempt": "1",
    }

    asyncio.run(
        launch_materialize(
            dagster_url="http://dagster.test/graphql",
            asset_key_path="silver/orders",
            job_name="orders_job",
            idempotency_key="request-42",
            tags=tags,
        )
    )
    asyncio.run(
        launch_retry(
            dagster_url="http://dagster.test/graphql",
            run_id="dagster-run",
            strategy="FROM_FAILURE",
            tags=tags,
        )
    )

    launch_tags = payloads[0]["variables"]["executionParams"]["executionMetadata"]["tags"]  # type: ignore[index]
    retry = payloads[1]["variables"]["reexecutionParams"]  # type: ignore[index]
    launch_tag_map = {tag["key"]: tag["value"] for tag in launch_tags}
    assert launch_tag_map["phlo/wap_branch"] == "pipeline-run-request-42"
    assert launch_tag_map["phlo/idempotency_key"] == "request-42"
    assert launch_tag_map["phlo/project_id"] == "warehouse"
    assert launch_tag_map["phlo/attempt"] == "1"
    assert retry["useParentRunTags"] is True
    assert {tag["key"]: tag["value"] for tag in retry["extraTags"]}["phlo/run_id"] == "request-42"


def test_wap_materialize_reconciles_a_lost_response_without_a_duplicate_launch(
    monkeypatch,
) -> None:
    accepted_run: dict[str, str] | None = None
    launch_calls = 0
    lookup_filters: list[dict[str, object]] = []

    async def fake_post(self, url, json=None, headers=None):  # noqa: ANN001, ANN202, ARG001
        nonlocal accepted_run, launch_calls
        if "ExistingMaterializationRun" in json["query"]:
            lookup_filters.append(json["variables"]["filter"])
            results = [accepted_run] if accepted_run else []
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"data": {"runsOrError": {"__typename": "Runs", "results": results}}},
            )

        launch_calls += 1
        accepted_run = {"runId": "dagster-run", "status": "STARTED"}
        raise httpx.ReadTimeout("Dagster accepted the run but the response was lost")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    kwargs = {
        "dagster_url": "http://dagster.test/graphql",
        "asset_key_path": "silver/orders",
        "job_name": "orders_job",
        "idempotency_key": "request-42",
        "tags": {
            "phlo/run_id": "request-42",
            "phlo/wap_branch": "pipeline-run-request-42",
        },
        "access_token": "verified-user-token",
    }

    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(launch_materialize(**kwargs))
    reconciled = asyncio.run(launch_materialize(**kwargs))

    assert launch_calls == 1
    assert reconciled.accepted is True
    assert reconciled.run_id == "dagster-run"
    assert reconciled.status == "STARTED"
    assert reconciled.details["reconciled"] is True
    assert lookup_filters == [
        {
            "tags": [
                {"key": "phlo/operation", "value": "materialize_asset"},
                {"key": "phlo/idempotency_key", "value": "request-42"},
            ]
        },
        {
            "tags": [
                {"key": "phlo/operation", "value": "materialize_asset"},
                {"key": "phlo/idempotency_key", "value": "request-42"},
            ]
        },
    ]


def test_terminate_maps_dagster_error(monkeypatch) -> None:
    async def fake_post(self, url, json=None, headers=None):  # noqa: ANN001, ANN202, ARG001
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "data": {
                    "terminateRun": {
                        "__typename": "RunNotFoundError",
                        "message": "missing run",
                    }
                }
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    result = asyncio.run(terminate(dagster_url="http://dagster.test/graphql", run_id="missing"))

    assert result.accepted is False
    assert result.status == "RunNotFoundError"
    assert result.message == "missing run"


def test_list_partitions_uses_asset_node_partition_dimensions(monkeypatch) -> None:
    async def fake_post(self, url, json=None, headers=None):  # noqa: ANN001, ANN202, ARG001
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "data": {
                    "assetNodeOrError": {
                        "__typename": "AssetNode",
                        "partitionKeysByDimension": [
                            {"name": "default", "partitionKeys": ["2025-01-01", "2025-01-02"]}
                        ],
                    }
                }
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    result = asyncio.run(
        list_partitions(dagster_url="http://dagster.test/graphql", asset_key_path="dlt_events")
    )

    assert result == [
        {"partition_key": "2025-01-01", "status": "UNKNOWN"},
        {"partition_key": "2025-01-02", "status": "UNKNOWN"},
    ]


def test_graphql_fails_before_http_when_production_identity_is_missing(
    monkeypatch,
) -> None:
    """Production orchestration->API calls must not send an anonymous request."""
    from phlo_dagster import operations as operations_module

    monkeypatch.setenv("PHLO_ENVIRONMENT", "production")
    monkeypatch.delenv("PHLO_SERVICE_CREDENTIALS_FILE", raising=False)
    monkeypatch.setattr(httpx.AsyncClient, "post", _never_called)

    with pytest.raises(RuntimeError, match="No service credential"):
        asyncio.run(
            operations_module._graphql(
                "http://phlo-api:4000/graphql",
                "query { __typename }",
                {},
            )
        )


def _never_called(*_args, **_kwargs):  # pragma: no cover - failure marker
    raise AssertionError("HTTP must not be contacted when production identity is missing")
