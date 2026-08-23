"""Bundled-stack ingest and transform contract checks.

Integration-marked: requires the live bundled stack. Verifies that
ingestion and dbt transforms share routing end to end — a mart built
over an ingested partition must contain exactly as many rows as the
raw table it reads through Trino.
"""

from __future__ import annotations

import pytest
from phlo_testing.profile_harness import BundledStackHarness
from phlo_trino import TrinoResource

pytestmark = pytest.mark.integration


def test_bundled_stack_ingest_and_transform_share_routing(
    bundled_stack_harness: BundledStackHarness,
) -> None:
    """Ingestion and dbt transforms should read and write through the same live routing."""
    partition_date = bundled_stack_harness.default_partition_date()

    bundled_stack_harness.materialize(
        "dlt_posts",
        partition_date=partition_date,
        timeout=1200,
        stream_output=True,
    )

    trino = TrinoResource(
        host="127.0.0.1", port=bundled_stack_harness.ports.trino, catalog="iceberg"
    )
    raw_rows = trino.execute("SELECT count(*) FROM posts", schema="raw")
    raw_count = raw_rows[0][0] if raw_rows else 0
    assert raw_count > 0

    bundled_stack_harness.materialize(
        "posts_mart",
        partition_date=partition_date,
        timeout=1200,
        stream_output=True,
    )

    mart_rows = trino.execute("SELECT count(*) FROM posts_mart", schema="raw_marts")
    mart_count = mart_rows[0][0] if mart_rows else 0
    assert mart_count == raw_count
