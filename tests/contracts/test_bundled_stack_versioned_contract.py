"""Bundled-stack versioned catalog contract checks.

End-to-end integration test: a Dagster materialization must write to an
isolated Nessie branch, promote it to main (wap_promoted tag), and drop the
branch afterwards, leaving queryable snapshots in Trino.
"""

from __future__ import annotations

import time

import pytest
from dagster import DagsterRunStatus
from phlo_nessie.resource import NessieResource
from phlo_testing.profile_harness import BundledStackHarness
from phlo_trino import TrinoResource

pytestmark = pytest.mark.integration


def test_bundled_stack_versioned_flow_uses_isolated_branch_then_promotes(
    bundled_stack_harness: BundledStackHarness,
) -> None:
    """Normal Dagster launch flow should isolate writes, then promote them to main."""
    partition_date = bundled_stack_harness.default_partition_date()

    assert bundled_stack_harness.list_table_snapshots(table_name="raw.posts", ref="main") == []

    run_id, branch_name = bundled_stack_harness.launch_versioned_materialization(
        "dlt_posts",
        partition_date=partition_date,
    )

    run_tags = bundled_stack_harness.get_run_tags(run_id)
    assert run_tags.get("phlo/wap_branch") == branch_name
    assert run_tags.get("phlo/ref") == branch_name
    assert run_tags.get("phlo/project_id") == bundled_stack_harness.project_dir.name
    assert run_tags.get("phlo/run_id") == branch_name.removeprefix("pipeline-run-")
    assert run_tags.get("phlo/attempt") == "1"

    bundled_stack_harness.wait_for_run_status(
        run_id,
        expected_statuses={
            DagsterRunStatus.STARTED,
            DagsterRunStatus.STARTING,
            DagsterRunStatus.SUCCESS,
        },
        timeout=180,
    )

    nessie = NessieResource(base_url=f"http://127.0.0.1:{bundled_stack_harness.ports.nessie}")
    assert any(branch.name == branch_name for branch in nessie.list_branches())

    bundled_stack_harness.wait_for_run_completion(run_id, timeout=1200)

    promotion_deadline = time.time() + 120
    while time.time() < promotion_deadline:
        tags = bundled_stack_harness.get_run_tags(run_id)
        if tags.get("phlo/wap_promoted") == "true":
            break
        time.sleep(1)
    assert bundled_stack_harness.get_run_tags(run_id).get("phlo/wap_promoted") == "true"

    bundled_stack_harness.wait_for_branch_absence(branch_name, timeout=120)

    main_snapshots = bundled_stack_harness.list_table_snapshots(table_name="raw.posts", ref="main")
    assert len(main_snapshots) > 0

    trino = TrinoResource(
        host="127.0.0.1",
        port=bundled_stack_harness.ports.trino,
        catalog="iceberg",
    )
    main_rows = trino.execute("SELECT count(*) FROM posts", schema="raw")
    assert (main_rows[0][0] if main_rows else 0) > 0
