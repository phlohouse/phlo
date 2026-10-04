"""Execute the multi-asset partition launch against disposable Dagster storage."""

import asyncio
import json
import time

import dagster as dg
from dagster._core.workspace.context import WorkspaceProcessContext
from dagster._core.workspace.load_target import PythonFileTarget
from dagster_graphql.test.utils import execute_dagster_graphql

from phlo_dagster import operations


def test_materialization_executes_partition_config_and_downstream_once(tmp_path, monkeypatch):
    definitions = tmp_path / "assets.py"
    definitions.write_text("""
import json
from pathlib import Path
import dagster as dg
parts = dg.DailyPartitionsDefinition(start_date="2026-09-24")
@dg.asset(partitions_def=parts, config_schema={"count": int})
def orders(context):
    count = context.op_config["count"]
    Path(__file__).with_name("orders.json").write_text(json.dumps({"count": count, "partition": context.partition_key, "ref": context.run.tags["phlo/ref"]}))
    return count
@dg.asset(partitions_def=parts)
def summary(orders):
    Path(__file__).with_name("summary.json").write_text(json.dumps({"total": orders * 7}))
    return orders * 7
config = dg.PartitionedConfig(partitions_def=parts, run_config_for_partition_key_fn=lambda key: {"ops": {"orders": {"config": {"count": int(key[-2:])}}}})
defs = dg.Definitions(assets=[orders, summary], jobs=[dg.define_asset_job("orders_job", config=config)])
""")
    (tmp_path / "instance").mkdir()
    overrides = {
        "run_coordinator": {
            "module": "dagster._core.run_coordinator.default_run_coordinator",
            "class": "DefaultRunCoordinator",
        }
    }
    with dg.instance_for_test(temp_dir=str(tmp_path / "instance"), overrides=overrides) as instance:
        target = PythonFileTarget(
            python_file=str(definitions),
            attribute="defs",
            working_directory=str(tmp_path),
            location_name="production_jobs",
        )
        with WorkspaceProcessContext(instance, target) as workspace:
            context = workspace.create_request_context()

            async def graphql(url, query, variables, **kwargs):
                result = await asyncio.to_thread(execute_dagster_graphql, context, query, variables)
                assert not result.errors, result.errors
                return {"data": result.data}

            monkeypatch.setattr(operations, "_graphql", graphql)
            partition_query = """query Partition($selector: PipelineSelector!, $partition: String!, $assets: [AssetKeyInput!]) {
              pipelineOrError(params: $selector) { ... on Pipeline {
                partition(partitionName: $partition, selectedAssetKeys: $assets) {
                  runConfigOrError { ... on PartitionRunConfig { yaml } }
                }
              } }
            }"""

            selector = {
                "pipelineName": "orders_job",
                "repositoryName": "__repository__",
                "repositoryLocationName": "production_jobs",
            }
            partition = execute_dagster_graphql(
                context,
                partition_query,
                {
                    "selector": selector,
                    "partition": "2026-09-25",
                    "assets": [{"path": ["orders"]}, {"path": ["summary"]}],
                },
            )
            assert not partition.errors, partition.errors
            import yaml

            config = yaml.safe_load(
                partition.data["pipelineOrError"]["partition"]["runConfigOrError"]["yaml"]
            )
            assert config == {"ops": {"orders": {"config": {"count": 25}}}}
            request = dict(
                dagster_url="unused",
                asset_key_path="orders",
                asset_selection=["orders", "summary"],
                job_name="orders_job",
                repository_name="__repository__",
                repository_location_name="production_jobs",
                partition_key="2026-09-25",
                run_config=config,
                idempotency_key="partition-request-1",
                tags={"environment": "prod", "phlo/ref": "prod-work"},
            )
            launched = asyncio.run(operations.launch_materialize(**request))
            assert launched.accepted, launched
            replay = asyncio.run(operations.launch_materialize(**request))
            assert replay.run_id == launched.run_id
            deadline = time.monotonic() + 60
            while (
                not instance.get_run_by_id(launched.run_id).is_finished
                and time.monotonic() < deadline
            ):
                time.sleep(0.2)
            run = instance.get_run_by_id(launched.run_id)
            assert run.status == dg.DagsterRunStatus.SUCCESS
            assert json.loads((tmp_path / "orders.json").read_text()) == {
                "count": 25,
                "partition": "2026-09-25",
                "ref": "prod-work",
            }
            assert json.loads((tmp_path / "summary.json").read_text()) == {"total": 175}
            assert len(instance.get_runs()) == 1
