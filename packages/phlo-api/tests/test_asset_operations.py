"""Asset operation plans, revision guards and owning-service payloads."""

import copy

import pytest
from fastapi import HTTPException
from graphql import parse, validate
from dagster_graphql.schema import create_schema

import test_v1_api
from test_v1_api import _asset_inventory_response
from phlo_api.api import v1_assets, v1_branch_workflows, operation_controls
from phlo_api.observatory_api import orchestrator_operations, run_action_contract

client = test_v1_api.client


@pytest.fixture
def operations(client, monkeypatch, tmp_path):
    http, *_ = client
    for name in ("SINGLE_REPLICA", "SINGLE_PROCESS", "REF_TAG_CONTRACT"):
        monkeypatch.setenv(f"PHLO_V1_ACTIONS_{name}", "1")
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setattr(
        operation_controls,
        "require_scope",
        lambda *_: {"subject": "alice", "scopes": ["lakehouse:operate"]},
    )
    monkeypatch.setattr(operation_controls, "enforce_rate_limit", lambda *_: None)
    monkeypatch.setattr(run_action_contract, "require_idempotency_key", lambda key: key)
    nodes = []
    for name, deps in [("orders", []), ("summary", [["warehouse", "orders"]])]:
        nodes.append(
            {
                "id": name,
                "assetKey": {"path": ["warehouse", name]},
                "description": None,
                "computeKind": "dbt",
                "kinds": ["dbt"],
                "groupName": "warehouse",
                "isMaterializable": True,
                "isPartitioned": True,
                "hasMaterializePermission": True,
                "opVersion": "code-1",
                "jobNames": ["warehouse_job"],
                "repository": {"name": "prod_repo", "location": {"name": "production_jobs"}},
                "dependencyKeys": [{"path": dep} for dep in deps],
                "assetMaterializations": [],
                "partitionDefinition": {"type": "TIME_WINDOW", "fmt": "%Y-%m-%d"},
                "partitionKeyConnection": {
                    "results": ["2026-09-27", "2026-09-26", "2026-09-25"],
                    "cursor": "",
                    "hasMore": False,
                },
            }
        )
    revision = {"hash": "head-1", "job": "job-1"}
    calls = []

    async def reference(name):
        return {"name": name, "type": "BRANCH", "hash": revision["hash"]}

    async def graphql(query, variables=None):
        if "V1Assets" in query or "AssetActionContext" in query:
            return _asset_inventory_response(nodes)
        if "AssetOperationNode" in query:
            selector = variables["selector"]
            scoped = [
                copy.deepcopy(node)
                for node in nodes
                if node["repository"]["name"] == selector["repositoryName"]
                and node["repository"]["location"]["name"] == selector["repositoryLocationName"]
            ]
            for node in scoped:
                node["partitionKeyConnection"]["results"] = node["partitionKeyConnection"][
                    "results"
                ][: variables["limit"]]
            return {
                "data": {
                    "repositoryOrError": {
                        "__typename": "Repository",
                        "name": selector["repositoryName"],
                        "location": {"name": selector["repositoryLocationName"]},
                        "assetNodes": scoped,
                    }
                }
            }
        if "V1BackfillPartitionSet" in query:
            return {
                "data": {
                    "partitionSetOrError": {
                        "__typename": "PartitionSet",
                        "pipelineName": "warehouse_job",
                        "repositoryOrigin": variables["repositorySelector"],
                    }
                }
            }
        if "AssetOperationJob" in query:
            return {
                "data": {
                    "pipelineOrError": {
                        "__typename": "Pipeline",
                        "pipelineSnapshotId": revision["job"],
                        "hasLaunchExecutionPermission": True,
                    }
                }
            }
        if "AssetOperationPartition" in query:
            return {
                "data": {
                    "pipelineOrError": {
                        "__typename": "Pipeline",
                        "partition": {
                            "runConfigOrError": {
                                "__typename": "PartitionRunConfig",
                                "yaml": f"ops:\n  ingest:\n    config:\n      date: '{variables['partition']}'",
                            }
                        },
                    }
                }
            }
        raise AssertionError("Actions must not query global asset definitions.")

    class Provider:
        async def materialize_asset(self, asset, request):
            calls.append((asset, copy.deepcopy(request)))
            return {"accepted": True, "run_id": f"run-{len(calls)}"}

        async def backfill_asset(self, asset, request):
            calls.append((asset, copy.deepcopy(request)))
            return {"accepted": True, "backfill_id": "backfill-1"}

    monkeypatch.setattr(v1_assets, "_graphql", graphql)
    monkeypatch.setattr(v1_branch_workflows, "_reference", reference)
    monkeypatch.setattr(
        orchestrator_operations, "resolve_orchestrator_operations", lambda: Provider()
    )
    return http, calls, revision, nodes


def estimate(http, **changes):
    params = {
        "env": "prod",
        "job_name": "warehouse_job",
        "mode": "backfill",
        "write_ref": "prod-work",
        "from_time": "2026-09-25T00:00:00Z",
        "to_time": "2026-09-27T00:00:00Z",
        "rebuild_downstream": "true",
        **changes,
    }
    return http.get(
        "/api/v1/assets/warehouse/orders/materialization-estimate",
        params={key: value for key, value in params.items() if value is not None},
    )


def action(plan_hash, **changes):
    return {
        "job_name": "warehouse_job",
        "mode": "backfill",
        "write_ref": "prod-work",
        "from_time": "2026-09-25T00:00:00Z",
        "to_time": "2026-09-27T00:00:00Z",
        "rebuild_downstream": True,
        "dry_run": False,
        "confirmed": True,
        "plan_hash": plan_hash,
        "idempotency_key": "operation-1",
        **changes,
    }


def test_plan_backfill_uses_real_partition_configs_and_downstream_selection(operations):
    http, calls, *_ = operations
    response = estimate(http)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["partition_keys"] == ["2026-09-25", "2026-09-26"]
    assert plan["partition_count"] == 2
    assert plan["selected_assets"] == ["warehouse/orders", "warehouse/summary"]
    assert plan["estimated_cost"] is None
    body = action(plan["plan_hash"])
    result = http.post("/api/v1/assets/warehouse/orders/materialize?env=prod", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["result"]["run_ids"] == ["run-1", "run-2"]
    assert (
        http.post("/api/v1/assets/warehouse/orders/materialize?env=prod", json=body).json()
        == result.json()
    )
    assert len(calls) == 2
    for index, (_, request) in enumerate(calls):
        assert request["partition_key"] == ["2026-09-25", "2026-09-26"][index]
        assert request["run_config"]["ops"]["ingest"]["config"]["date"] == request["partition_key"]
        assert request["asset_selection"] == ["warehouse/orders", "warehouse/summary"]
        assert request["tags"]["phlo/ref"] == "prod-work"
        assert request["tags"]["phlo/ref_hash"] == "head-1"
        assert request["repository_location_name"] == "production_jobs"
    assert calls[0][1]["idempotency_key"] != calls[1][1]["idempotency_key"]


@pytest.mark.parametrize("revision_field", ["hash", "job"])
def test_changed_revision_refuses_execution(operations, revision_field):
    http, calls, revision, _ = operations
    plan = estimate(http).json()
    revision[revision_field] = "changed"
    response = http.post(
        "/api/v1/assets/warehouse/orders/materialize?env=prod", json=action(plan["plan_hash"])
    )
    assert response.status_code == 409
    assert calls == []


def test_scoped_refs_downstream_permissions_and_unsupported_full_refresh(operations):
    http, calls, _, nodes = operations
    assert estimate(http, write_ref="candidate").status_code == 404
    nodes[1]["hasMaterializePermission"] = False
    assert estimate(http).status_code == 403
    nodes[1]["hasMaterializePermission"] = True
    nodes[0]["kinds"] = ["dlt"]
    assert estimate(http, mode="full", from_time=None, to_time=None).status_code == 422
    assert calls == []


def test_full_refresh_requires_work_branch_confirmation_and_mfa(operations):
    http, calls, *_ = operations
    assert (
        estimate(http, mode="full", from_time=None, to_time=None, write_ref="main").status_code
        == 403
    )
    plan = estimate(http, mode="full", from_time=None, to_time=None).json()
    body = action(plan["plan_hash"], mode="full", from_time=None, to_time=None)
    assert (
        http.post(
            "/api/v1/assets/warehouse/orders/materialize?env=prod",
            json={**body, "confirmed": False},
        ).status_code
        == 422
    )
    assert (
        http.post("/api/v1/assets/warehouse/orders/materialize?env=prod", json=body).status_code
        == 403
    )
    assert calls == []


def test_full_refresh_accepts_recent_verified_mfa_and_targets_only_root(operations, monkeypatch):
    from datetime import UTC, datetime
    from phlo.capabilities import AuthPrincipal, AuthResult, AuthenticatedSession
    from phlo_api.api import authentication

    http, calls, *_ = operations
    principal = AuthPrincipal(
        subject="alice",
        principal_type="user",
        claims={"amr": ["mfa"], "auth_time": datetime.now(UTC).timestamp()},
    )
    session = AuthenticatedSession(
        principal=principal,
        auth_method="bearer_token",
        provider_name="jwt",
        attributes={
            "jwt_issuer": "issuer",
            "jwt_audience": "audience",
            "jwt_issuer_validated": "true",
            "jwt_audience_validated": "true",
        },
    )
    monkeypatch.setattr(
        authentication,
        "authenticate_request",
        lambda request: AuthResult(authenticated=True, principal=principal, session=session),
    )
    plan = estimate(http, mode="full", from_time=None, to_time=None).json()
    response = http.post(
        "/api/v1/assets/warehouse/orders/materialize?env=prod",
        json=action(plan["plan_hash"], mode="full", from_time=None, to_time=None),
    )
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert calls[0][1]["partition_key"] is None
    assert calls[0][1]["tags"]["phlo/full_refresh_asset"] == "warehouse/orders"


def test_adapter_checks_every_selected_asset_permission(monkeypatch):
    import asyncio
    from phlo_api.observatory_api import dagster

    async def details(key):
        return {"has_materialize_permission": key == "orders", "op_names": [key]}

    monkeypatch.setattr(dagster, "get_asset_details", details)
    response = asyncio.run(
        dagster.materialize_asset(
            "orders",
            dagster.MaterializeAssetRequest(
                dry_run=False,
                job_name="orders_job",
                asset_selection=["orders", "summary"],
            ),
        )
    )
    assert response.accepted is False
    assert response.status == "NOT_MATERIALIZABLE"
    with pytest.raises(HTTPException):
        asyncio.run(
            dagster.materialize_asset(
                "orders", dagster.MaterializeAssetRequest(asset_selection=["summary"])
            )
        )


def test_time_range_rejects_partial_partition_and_naive_boundaries():
    keys = ["2026-09-25", "2026-09-27", "2026-09-26"]
    assert v1_assets._time_range_keys(
        keys, "%Y-%m-%d", "2026-09-25T00:00:00Z", "2026-09-27T00:00:00Z"
    ) == ["2026-09-25", "2026-09-26"]
    for start, end in [
        ("2026-09-25T00:00:00", "2026-09-27T00:00:00Z"),
        ("2026-09-25T00:00:00Z", "2026-09-26T12:00:00Z"),
        ("2026-09-24T00:00:00Z", "2026-09-27T00:00:00Z"),
    ]:
        with pytest.raises(HTTPException):
            v1_assets._time_range_keys(keys, "%Y-%m-%d", start, end)


def test_plan_queries_match_installed_dagster_schema():
    from phlo_api.observatory_api import dagster

    schema = create_schema().graphql_schema
    for query in (
        v1_assets.ASSET_QUERY,
        v1_assets.ASSET_DETAIL_QUERY,
        v1_assets._OPERATION_NODE_QUERY,
        v1_assets._OPERATION_JOB_QUERY,
        v1_assets._OPERATION_PARTITION_QUERY,
        dagster._ASSET_OPERATION_PERMISSION_QUERY,
    ):
        assert validate(schema, parse(query)) == []


def test_duplicate_keys_keep_estimate_materialize_and_latest_backfill_scoped(operations):
    http, calls, _, nodes = operations
    stage = copy.deepcopy(nodes)
    for node in stage:
        node["repository"] = {"name": "stage_repo", "location": {"name": "testing_jobs"}}
        node["partitionKeyConnection"]["results"] = ["2026-09-26", "2026-09-25", "2026-09-24"]
    nodes.extend(stage)
    # Prod is first and has newer partitions; a global lookup would reject staging
    # or silently choose the production partition instead.
    plan = estimate(
        http, env="staging", write_ref="candidate", mode="latest", from_time=None, to_time=None
    )
    assert plan.status_code == 200, plan.text
    assert plan.json()["partition_keys"] == ["2026-09-26"]
    response = http.post(
        "/api/v1/assets/warehouse/orders/materialize?env=staging",
        json=action(
            plan.json()["plan_hash"],
            mode="latest",
            write_ref="candidate",
            from_time=None,
            to_time=None,
        ),
    )
    assert response.status_code == 200, response.text
    response = http.post(
        "/api/v1/assets/warehouse/orders/backfill?env=staging",
        json={
            "job_name": "warehouse_job",
            "partition_set_name": "daily",
            "selection": "latest",
            "idempotency_key": "stage-backfill",
        },
    )
    assert response.status_code == 200, response.text
    assert len(calls) == 2
    assert calls[0][1]["partition_key"] == "2026-09-26"
    assert calls[1][1]["partitions"] == ["2026-09-26"]
    for _, request in calls:
        assert request["repository_location_name"] == "testing_jobs"
        assert request["repository_name"] == "stage_repo"
        assert request["tags"]["phlo/ref"] == "candidate"
    nodes.append(copy.deepcopy(stage[0]))
    assert estimate(http, env="staging", write_ref="candidate").status_code == 503
    assert len(calls) == 2


def test_duplicate_definitions_execute_in_requested_repository(operations, monkeypatch, tmp_path):
    import asyncio
    import json
    import time
    import dagster as dg
    from dagster._core.workspace.context import WorkspaceProcessContext
    from dagster._core.workspace.load_target import CompositeTarget, PythonFileTarget
    from dagster_graphql.test.utils import execute_dagster_graphql
    from phlo_dagster import operations as provider
    from phlo_api.observatory_api import dagster as adapter

    http, *_ = operations
    targets = []
    for location, factor in [("production_jobs", 1), ("testing_jobs", 5)]:
        directory = tmp_path / location
        directory.mkdir()
        definitions = directory / "definitions.py"
        definitions.write_text(
            f"""
import json
from pathlib import Path
import dagster as dg
parts = dg.DailyPartitionsDefinition(start_date="2026-09-24")
@dg.asset(key_prefix="warehouse", partitions_def=parts, config_schema={{"count": int}})
def orders(context):
    count = context.op_config["count"]
    Path(__file__).with_name("executed.json").write_text(json.dumps({{"count": count, "partition": context.partition_key, "ref": context.run.tags["phlo/ref"]}}))
    return count
@dg.asset(key_prefix="warehouse", partitions_def=parts, ins={{"orders": dg.AssetIn(key=["warehouse", "orders"])}})
def summary(orders):
    Path(__file__).with_name("summary.json").write_text(json.dumps({{"total": orders * 7}}))
    return orders * 7
def partition_config(key):
    return dict(ops=dict(warehouse__orders=dict(config=dict(count=int(key[-2:]) * {factor}))))
config = dg.PartitionedConfig(partitions_def=parts, run_config_for_partition_key_fn=partition_config)
defs = dg.Definitions(assets=[orders, summary], jobs=[dg.define_asset_job("warehouse_job", config=config)])
""",
            encoding="utf-8",
        )
        targets.append(PythonFileTarget(str(definitions), "defs", str(directory), location))
    instance_dir = tmp_path / "dagster"
    instance_dir.mkdir()
    overrides = {
        "run_coordinator": {
            "module": "dagster._core.run_coordinator.default_run_coordinator",
            "class": "DefaultRunCoordinator",
        }
    }
    with dg.DagsterInstance.local_temp(str(instance_dir), overrides=overrides) as instance:
        with WorkspaceProcessContext(instance, CompositeTarget(targets)) as workspace:
            context = workspace.create_request_context()

            async def graphql(query, variables=None):
                result = await asyncio.to_thread(execute_dagster_graphql, context, query, variables)
                assert not result.errors, result.errors
                return {"data": result.data}

            async def transport(url, query, variables, **kwargs):
                return await graphql(query, variables)

            monkeypatch.setattr(v1_assets, "_graphql", graphql)
            monkeypatch.setattr(adapter, "graphql_request", transport)
            monkeypatch.setattr(provider, "_graphql", transport)
            monkeypatch.setattr(
                orchestrator_operations,
                "resolve_orchestrator_operations",
                lambda: orchestrator_operations.LegacyDagsterOrchestratorOperationsProvider(),
            )
            plan = estimate(http, env="staging", write_ref="candidate")
            assert plan.status_code == 200, plan.text
            assert plan.json()["partition_keys"] == ["2026-09-25", "2026-09-26"]
            body = action(plan.json()["plan_hash"], write_ref="candidate")
            response = http.post(
                "/api/v1/assets/warehouse/orders/materialize?env=staging", json=body
            )
            assert response.status_code == 200, response.text
            assert response.json()["result"]["accepted"] is True
            assert (
                http.post(
                    "/api/v1/assets/warehouse/orders/materialize?env=staging", json=body
                ).json()
                == response.json()
            )
            run_ids = response.json()["result"]["run_ids"]
            assert len(run_ids) == 2
            deadline = time.monotonic() + 60
            while (
                not all(instance.get_run_by_id(run_id).is_finished for run_id in run_ids)
                and time.monotonic() < deadline
            ):
                time.sleep(0.2)
            for run_id in run_ids:
                run = instance.get_run_by_id(run_id)
                assert run.status == dg.DagsterRunStatus.SUCCESS
                assert (
                    run.remote_job_origin.repository_origin.code_location_origin.location_name
                    == "testing_jobs"
                )
                assert run.tags["phlo/ref"] == "candidate"
            assert len(instance.get_runs()) == 2
            assert not (tmp_path / "production_jobs" / "executed.json").exists()
            executed = json.loads((tmp_path / "testing_jobs" / "executed.json").read_text())
            assert executed["count"] in (125, 130)
            assert json.loads((tmp_path / "testing_jobs" / "summary.json").read_text())[
                "total"
            ] in (875, 910)
            response = http.post(
                "/api/v1/assets/warehouse/orders/backfill?env=staging",
                json={
                    "job_name": "warehouse_job",
                    "partition_set_name": "warehouse_job_partition_set",
                    "selection": "latest",
                    "idempotency_key": "native-backfill",
                    "dry_run": False,
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["result"]["accepted"] is True
            backfill = instance.get_backfills()[0]
            assert (
                backfill.partition_set_origin.repository_origin.code_location_origin.location_name
                == "testing_jobs"
            )
            assert backfill.tags["phlo/ref"] == "candidate"
            assert len(backfill.partition_names) == 1


def test_automatic_plan_discovers_common_implicit_job_and_launches_selection(operations):
    http, calls, _, nodes = operations
    nodes[0]["jobNames"] = ["warehouse_job", "__ASSET_JOB_7", "__ASSET_JOB_3"]
    nodes[1]["jobNames"] = ["warehouse_job", "__ASSET_JOB_7"]
    plan = estimate(http, job_name=None).json()
    assert plan["job_name"] == "__ASSET_JOB_7"
    assert plan["job_selection"] == "automatic"
    assert plan["partition_count"] == 2
    assert plan["selected_assets"] == ["warehouse/orders", "warehouse/summary"]
    assert all(
        plan[field] is None
        for field in (
            "estimated_rows",
            "estimated_bytes",
            "estimated_duration_seconds",
            "estimated_cost",
        )
    )
    body = action(plan["plan_hash"])
    body.pop("job_name")
    response = http.post("/api/v1/assets/warehouse/orders/materialize?env=prod", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["job_name"] == "__ASSET_JOB_7"
    assert len(calls) == 2
    assert all(call[1]["job_name"] == "__ASSET_JOB_7" for call in calls)
    assert all(
        call[1]["asset_selection"] == ["warehouse/orders", "warehouse/summary"] for call in calls
    )


def test_caller_supplied_count_is_not_a_verified_or_launchable_plan(operations):
    http, calls, *_ = operations
    response = estimate(http, job_name=None, partition_count=3)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["partition_count"] == 3
    assert value["plan_hash"] is None
    assert value["job_name"] is None
    assert (
        value["workload_status"]
        == "Caller-supplied run count; no launch plan has been verified and no workload source is configured."
    )
    response = http.post(
        "/api/v1/assets/warehouse/orders/materialize?env=prod", json=action(value["plan_hash"])
    )
    assert response.status_code == 409
    assert calls == []


def test_automatic_plan_requires_unique_common_job_and_all_permissions(operations):
    http, calls, _, nodes = operations
    assert estimate(http, job_name=None).status_code == 422
    for node in nodes:
        node["jobNames"] += ["__ASSET_JOB_3", "__ASSET_JOB_7"]
    assert estimate(http, job_name=None).status_code == 422
    assert estimate(http).status_code == 200  # Explicit configured job remains valid.
    nodes[0]["jobNames"].remove("__ASSET_JOB_3")
    nodes[1]["hasMaterializePermission"] = False
    assert estimate(http, job_name=None).status_code == 403
    assert calls == []


@pytest.mark.parametrize("changed", ["job_name", "selected_assets", "op_version"])
def test_automatic_plan_pins_job_identity_selection_and_code(operations, changed):
    http, calls, _, nodes = operations
    for node in nodes:
        node["jobNames"] += ["__ASSET_JOB_7"]
    plan = estimate(http, job_name=None).json()
    body = action(plan["plan_hash"])
    body.pop("job_name")
    if changed == "job_name":
        for node in nodes:
            node["jobNames"] = ["warehouse_job", "__ASSET_JOB_8"]
    elif changed == "selected_assets":
        body["rebuild_downstream"] = False
    else:
        nodes[1]["opVersion"] = "code-2"
    response = http.post("/api/v1/assets/warehouse/orders/materialize?env=prod", json=body)
    assert response.status_code == 409
    assert calls == []


@pytest.mark.parametrize("partitioned", [False, True])
def test_native_implicit_job_executes_only_selected_asset(
    operations, monkeypatch, tmp_path, partitioned
):
    import asyncio
    import time
    import dagster as dg
    from dagster._core.workspace.context import WorkspaceProcessContext
    from dagster._core.workspace.load_target import PythonFileTarget
    from dagster_graphql.test.utils import execute_dagster_graphql
    from phlo_api.observatory_api import dagster as adapter
    from phlo_dagster import operations as provider

    http, *_ = operations
    definitions = tmp_path / "definitions.py"
    definitions.write_text(
        f"""
from pathlib import Path
import dagster as dg
parts = dg.DailyPartitionsDefinition(start_date="2026-09-24") if {partitioned!r} else None
@dg.asset(key_prefix="warehouse", partitions_def=parts)
def orders(context):
    Path(__file__).with_name("executed.txt").write_text(context.run.tags["phlo/ref"])
    return 19
@dg.asset(key_prefix="warehouse", deps=[orders], partitions_def=parts)
def summary():
    raise AssertionError("Unselected asset must not execute")
defs = dg.Definitions(assets=[orders, summary])
""",
        encoding="utf-8",
    )
    instance_dir = tmp_path / "dagster"
    instance_dir.mkdir()
    overrides = {
        "run_coordinator": {
            "module": "dagster._core.run_coordinator.default_run_coordinator",
            "class": "DefaultRunCoordinator",
        }
    }
    with dg.DagsterInstance.local_temp(str(instance_dir), overrides=overrides) as instance:
        target = PythonFileTarget(str(definitions), "defs", str(tmp_path), "production_jobs")
        with WorkspaceProcessContext(instance, target) as workspace:
            context = workspace.create_request_context()

            async def graphql(query, variables=None):
                result = await asyncio.to_thread(execute_dagster_graphql, context, query, variables)
                assert not result.errors, result.errors
                return {"data": result.data}

            async def transport(url, query, variables, **kwargs):
                return await graphql(query, variables)

            monkeypatch.setattr(v1_assets, "_graphql", graphql)
            monkeypatch.setattr(adapter, "graphql_request", transport)
            monkeypatch.setattr(provider, "_graphql", transport)
            monkeypatch.setattr(
                orchestrator_operations,
                "resolve_orchestrator_operations",
                lambda: orchestrator_operations.LegacyDagsterOrchestratorOperationsProvider(),
            )
            mode = "backfill" if partitioned else "latest"
            start = "2026-09-25T00:00:00Z" if partitioned else None
            end = "2026-09-27T00:00:00Z" if partitioned else None
            response = estimate(
                http,
                job_name=None,
                mode=mode,
                from_time=start,
                to_time=end,
                rebuild_downstream=False,
            )
            assert response.status_code == 200, response.text
            plan = response.json()
            assert plan["job_name"] == "__ASSET_JOB"
            assert plan["selected_assets"] == ["warehouse/orders"]
            assert plan["partition_keys"] == (["2026-09-25", "2026-09-26"] if partitioned else [])
            body = action(
                plan["plan_hash"],
                mode=mode,
                from_time=start,
                to_time=end,
                rebuild_downstream=False,
            )
            body.pop("job_name")
            response = http.post("/api/v1/assets/warehouse/orders/materialize?env=prod", json=body)
            assert response.status_code == 200, response.text
            result = response.json()["result"]
            assert result["accepted"] is True
            run_ids = result["run_ids"]
            assert len(run_ids) == (2 if partitioned else 1)
            deadline = time.monotonic() + 60
            while (
                not all(instance.get_run_by_id(run_id).is_finished for run_id in run_ids)
                and time.monotonic() < deadline
            ):
                time.sleep(0.2)
            for run_id in run_ids:
                run = instance.get_run_by_id(run_id)
                assert run.status == dg.DagsterRunStatus.SUCCESS
                assert run.job_name == "__ASSET_JOB"
                assert run.asset_selection == {dg.AssetKey(["warehouse", "orders"])}
                assert run.tags["phlo/plan"] == plan["plan_hash"]
            if partitioned:
                assert {
                    instance.get_run_by_id(run_id).tags["dagster/partition"] for run_id in run_ids
                } == {"2026-09-25", "2026-09-26"}
            assert (tmp_path / "executed.txt").read_text(encoding="utf-8") == "prod-work"
