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
    launch_backfill,
    get_run_status,
    launch_materialize,
    launch_retry,
    list_partitions,
    terminate,
    wait_for_dagster_http,
)


@pytest.fixture(autouse=True)
def launch_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("PHLO_OPERATIONS_JOURNAL_DIR", str(tmp_path / "journal"))


def test_materialize_with_empty_journal_does_not_duplicate_an_unbound_run(monkeypatch):
    async def post(self, url, json=None, headers=None):
        assert "ExistingMaterializationRun" in json["query"], "must not submit another run"
        tags = {tag["key"]: tag["value"] for tag in json["variables"]["filter"]["tags"]}
        runs = [] if "phlo/launch_request" in tags else [{"runId": "existing", "status": "STARTED"}]
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"data": {"runsOrError": {"__typename": "Runs", "results": runs}}},
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    request = dict(
        dagster_url="http://dagster.test/graphql",
        asset_key_path="silver/orders",
        job_name="orders_job",
        idempotency_key="existing-key",
    )
    with pytest.raises(RuntimeError, match="reconciliation required"):
        asyncio.run(launch_materialize(**request))
    with pytest.raises(RuntimeError, match="unknown"):
        asyncio.run(launch_materialize(**request))


@pytest.mark.parametrize("expiry_scan", [False, True])
def test_materialize_expired_unsubmitted_claim_returns_a_durable_rejection(
    tmp_path, monkeypatch, expiry_scan
):
    from dataclasses import replace

    from phlo.operations.journal import OperationJournal
    from phlo.operations.journal_store import FileOperationJournalStore

    async def post(*args, **kwargs):
        pytest.fail("an unsubmitted expired claim must not contact Dagster")

    monkeypatch.setattr(httpx.AsyncClient, "post", post)

    class InterruptedStore(FileOperationJournalStore):
        def claim(self, entry):
            assert super().claim(replace(entry, claim_expiry="2000-01-01T00:00:00+00:00"))
            raise RuntimeError("interrupted before submission")

    request = dict(
        dagster_url="http://dagster.invalid/graphql",
        asset_key_path="silver/orders",
        job_name="orders_job",
        idempotency_key="interrupted-key",
    )
    directory = tmp_path / "interrupted-journal"
    with pytest.raises(RuntimeError, match="interrupted before submission"):
        asyncio.run(launch_materialize(**request, journal_store=InterruptedStore(directory)))
    if expiry_scan:
        OperationJournal(FileOperationJournalStore(directory)).claim(
            operation_id="unrelated",
            subject="another endpoint",
            action="materialize_asset",
            target="another target",
            plan_token="another request",
        )
    with pytest.raises(RuntimeError, match="binding_mismatch"):
        asyncio.run(
            launch_materialize(
                **{**request, "job_name": "different_job"},
                journal_store=FileOperationJournalStore(directory),
            )
        )
    result = asyncio.run(
        launch_materialize(**request, journal_store=FileOperationJournalStore(directory))
    )
    assert result.accepted is False
    assert result.status == "NOT_SUBMITTED"
    assert "expired" in result.message
    assert result.run_id is None
    assert (
        asyncio.run(
            launch_materialize(**request, journal_store=FileOperationJournalStore(directory))
        )
        == result
    )


@pytest.mark.parametrize("visible", [False, True])
def test_materialize_claim_prevents_concurrent_duplicate_submission(monkeypatch, visible):
    launches = 0
    request = dict(
        dagster_url="http://dagster.test/graphql",
        asset_key_path="silver/orders",
        job_name="orders_job",
        idempotency_key="concurrent-launch",
    )

    async def exercise():
        submitted = asyncio.Event()
        release = asyncio.Event()

        async def fake_post(self, url, json=None, headers=None):
            nonlocal launches
            if "ExistingMaterializationRun" in json["query"]:
                runs = (
                    [{"runId": "physical-1", "status": "STARTED"}] if visible and launches else []
                )
                data = {"runsOrError": {"__typename": "Runs", "results": runs}}
            else:
                launches += 1
                run_id = f"physical-{launches}"
                submitted.set()
                if launches == 1:
                    await release.wait()
                data = {
                    "launchPipelineExecution": {
                        "__typename": "LaunchRunSuccess",
                        "run": {"runId": run_id, "status": "STARTED"},
                    }
                }
            return httpx.Response(200, request=httpx.Request("POST", url), json={"data": data})

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        first = asyncio.create_task(launch_materialize(**request))
        await submitted.wait()
        try:
            if visible:
                assert (await launch_materialize(**request)).run_id == "physical-1"
            else:
                with pytest.raises(RuntimeError, match="in progress|unknown"):
                    await launch_materialize(**request)
        finally:
            release.set()
            result = await first
        assert result.run_id == "physical-1"
        assert (await launch_materialize(**request)).run_id == "physical-1"

    asyncio.run(exercise())
    assert launches == 1


@pytest.mark.parametrize("failure", ["timeout", "cancellation", "PythonError", "missing_run_id"])
def test_materialize_unknown_without_visible_run_never_resubmits(monkeypatch, failure):
    launches = 0

    async def fake_post(self, url, json=None, headers=None):
        nonlocal launches
        if "ExistingMaterializationRun" in json["query"]:
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"data": {"runsOrError": {"__typename": "Runs", "results": []}}},
            )
        launches += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("response lost")
        if failure == "cancellation":
            raise asyncio.CancelledError
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "data": {
                    "launchPipelineExecution": {
                        "__typename": "PythonError"
                        if failure == "PythonError"
                        else "LaunchRunSuccess",
                        "message": "uncertain outcome",
                    }
                }
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    request = dict(
        dagster_url="http://dagster.test/graphql",
        asset_key_path="silver/orders",
        job_name="orders_job",
        idempotency_key="unknown-launch",
    )
    expected = {"timeout": httpx.ReadTimeout, "cancellation": asyncio.CancelledError}.get(
        failure, RuntimeError
    )
    with pytest.raises(expected):
        asyncio.run(launch_materialize(**request))
    with pytest.raises(RuntimeError, match="unknown"):
        asyncio.run(launch_materialize(**request))
    assert launches == 1


def test_materialize_replay_is_bound_to_exact_request(monkeypatch):
    launches = 0

    async def fake_post(self, url, json=None, headers=None):
        nonlocal launches
        if "ExistingMaterializationRun" in json["query"]:
            data = {"runsOrError": {"__typename": "Runs", "results": []}}
        else:
            launches += 1
            data = {
                "launchPipelineExecution": {
                    "__typename": "LaunchRunSuccess",
                    "run": {"runId": f"physical-{launches}", "status": "STARTED"},
                }
            }
        return httpx.Response(200, request=httpx.Request("POST", url), json={"data": data})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    request = dict(
        dagster_url="http://dagster.test/graphql",
        asset_key_path="silver/orders",
        job_name="orders_job",
        partition_key="2026-09-24",
        idempotency_key="bound-launch",
    )
    original = asyncio.run(launch_materialize(**request))
    assert asyncio.run(launch_materialize(**request)) == original
    with pytest.raises(RuntimeError, match="binding_mismatch"):
        asyncio.run(launch_materialize(**{**request, "partition_key": "2026-09-25"}))
    assert launches == 1


def test_keyed_materialize_requires_durable_store_before_contacting_dagster(monkeypatch):
    monkeypatch.delenv("PHLO_OPERATIONS_JOURNAL_DIR")
    with pytest.raises(RuntimeError, match="requires PHLO_OPERATIONS_JOURNAL_DIR"):
        asyncio.run(
            launch_materialize(
                dagster_url="http://dagster.invalid/graphql",
                asset_key_path="orders",
                job_name="orders_job",
                idempotency_key="requires-store",
            )
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


def test_launch_backfill_uses_native_all_partitions_and_pins_repository(monkeypatch) -> None:
    captured: dict[str, object] = {}

    # reason: Match AsyncClient.post's mock call shape; only the captured request is asserted.
    async def fake_post(self, url, json=None, headers=None):  # noqa: ANN001, ANN202, ARG001
        captured["json"] = json
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "data": {
                    "launchPartitionBackfill": {
                        "__typename": "LaunchBackfillSuccess",
                        "backfillId": "backfill-1",
                        "launchedRunIds": [],
                    }
                }
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    result = asyncio.run(
        launch_backfill(
            dagster_url="http://dagster.test/graphql",
            asset_key_path="silver/orders",
            partition_set_name="orders_daily",
            partition_keys=[],
            all_partitions=True,
            job_name="orders_job",
            repository_location_name="production_jobs",
            repository_name="warehouse_repo",
            tags={"environment": "prod", "phlo/ref": "main"},
        )
    )

    assert result.accepted is True
    assert result.details == {"partitions": [], "partition_count": None, "all_partitions": True}
    # reason: The mocked GraphQL request above captures this exact nested JSON shape.
    params = captured["json"]["variables"]["backfillParams"]  # type: ignore[index]
    assert params["selector"] == {
        "partitionSetName": "orders_daily",
        "repositorySelector": {
            "repositoryLocationName": "production_jobs",
            "repositoryName": "warehouse_repo",
        },
    }
    assert params["allPartitions"] is True
    assert "partitionNames" not in params
    assert params["assetSelection"] == [{"path": ["silver", "orders"]}]
    tags = {tag["key"]: tag["value"] for tag in params["tags"]}
    assert tags["phlo/asset_key"] == "silver/orders"
    assert tags["phlo/job"] == "orders_job"
    assert tags["environment"] == "prod"
    assert tags["phlo/ref"] == "main"


def test_launch_backfill_rejects_unbounded_or_ambiguous_selection() -> None:
    with pytest.raises(ValueError, match="Specify either"):
        asyncio.run(
            launch_backfill(
                dagster_url="http://dagster.test/graphql",
                asset_key_path="silver/orders",
                partition_set_name="orders_daily",
                partition_keys=[],
            )
        )


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


@pytest.mark.parametrize("wap", [False, True])
def test_retry_advances_logical_attempt_or_requires_new_wap_binding(monkeypatch, wap) -> None:
    payloads: list[dict[str, object]] = []

    async def fake_post(self, url, json=None, headers=None):  # noqa: ANN001, ANN202, ARG001
        if "RunStatus" in json["query"]:
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "data": {
                        "runOrError": {
                            "__typename": "Run",
                            "runId": "dagster-run",
                            "status": "FAILURE",
                            "rootRunId": "physical-root",
                            "tags": [{"key": key, "value": value} for key, value in tags.items()],
                        }
                    }
                },
            )
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
        "phlo/project_id": "warehouse",
        "phlo/attempt": "3",
    }
    if wap:
        tags["phlo/wap_branch"] = "pipeline-run-request-42"

    asyncio.run(
        launch_materialize(
            dagster_url="http://dagster.test/graphql",
            asset_key_path="silver/orders",
            job_name="orders_job",
            idempotency_key="request-42",
            tags=tags,
        )
    )
    if wap:
        with pytest.raises(ValueError, match="newly prepared launch"):
            asyncio.run(
                launch_retry(
                    dagster_url="http://dagster.test/graphql",
                    run_id="dagster-run",
                    strategy="FROM_FAILURE",
                    tags=tags,
                )
            )
        assert len(payloads) == 1
        return
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
    assert launch_tag_map["phlo/idempotency_key"] == "request-42"
    assert launch_tag_map["phlo/project_id"] == "warehouse"
    assert launch_tag_map["phlo/attempt"] == "3"
    assert retry["useParentRunTags"] is True
    assert {tag["key"]: tag["value"] for tag in retry["extraTags"]}["phlo/run_id"] == "request-42"
    assert {tag["key"]: tag["value"] for tag in retry["extraTags"]}["phlo/attempt"] == "4"


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
    lookup_tags = {tag["key"]: tag["value"] for tag in lookup_filters[-1]["tags"]}
    assert lookup_tags["phlo/operation"] == "materialize_asset"
    assert lookup_tags["phlo/idempotency_key"] == "request-42"
    assert len(lookup_tags["phlo/launch_request"]) == 64


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


def test_wait_for_dagster_http_returns_on_first_answer(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_post(self, url, json=None, headers=None):
        calls.append(url)
        return httpx.Response(200, request=httpx.Request("POST", url), json={"data": {}})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    asyncio.run(wait_for_dagster_http("http://dagster.test/graphql"))

    assert calls == ["http://dagster.test/graphql"]


def test_wait_for_dagster_http_retries_transport_failures(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_post(self, url, json=None, headers=None):
        calls.append(url)
        if len(calls) < 3:
            raise httpx.ConnectError("warming up")
        return httpx.Response(200, request=httpx.Request("POST", url), json={"data": {}})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    asyncio.run(wait_for_dagster_http("http://dagster.test/graphql", poll_interval_seconds=0))

    assert calls == ["http://dagster.test/graphql"] * 3


def test_wait_for_dagster_http_times_out_with_actionable_error(monkeypatch) -> None:
    async def fake_post(self, url, json=None, headers=None):
        raise httpx.ConnectError("still down")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    with pytest.raises(RuntimeError, match="not accepting GraphQL requests"):
        asyncio.run(
            wait_for_dagster_http(
                "http://dagster.test/graphql",
                timeout_seconds=0,
                poll_interval_seconds=0,
            )
        )


def test_launch_materialize_never_injects_console_level(monkeypatch) -> None:
    """GraphQL launches pass run config through untouched: the console-level
    decision is made inside the worker at scope entry, where whether the
    SDK can actually drive the pretty drain is knowable. A host-side guess —
    based on environment flags — must never quiet a console that has no drain
    replacing it."""
    captured: list[dict[str, object]] = []

    async def fake_post(self, url, json=None, headers=None):  # noqa: ANN001, ANN202, ARG001
        captured.append(json)
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
    # Pretty opted in on the host shell — must still not reach the payload.
    monkeypatch.setenv("PHLO_OBSERVE_PRETTY", "1")

    def _run_config() -> dict[str, object]:
        return captured[-1]["variables"]["executionParams"]["runConfigData"]  # type: ignore[index]

    asyncio.run(
        launch_materialize(
            dagster_url="http://dagster.test/graphql",
            asset_key_path="silver/orders",
            job_name="orders_job",
        )
    )
    assert _run_config() == {}

    asyncio.run(
        launch_materialize(
            dagster_url="http://dagster.test/graphql",
            asset_key_path="silver/orders",
            job_name="orders_job",
            run_config={"ops": {"asset": {"config": {"x": 1}}}},
        )
    )
    assert _run_config() == {"ops": {"asset": {"config": {"x": 1}}}}
