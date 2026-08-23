"""Bundled-stack publish contract checks.

Asserts the bundled stack publishes committed marts only, exercised end-to-end
through the bundled_stack_harness fixture.
"""

from __future__ import annotations

import psycopg2
import pytest
from phlo_testing.profile_harness import BundledStackHarness
from phlo_trino import TrinoResource
from psycopg2 import sql

pytestmark = pytest.mark.integration


def test_bundled_stack_publish_uses_committed_marts_only(
    bundled_stack_harness: BundledStackHarness,
) -> None:
    """Publishing should expose the committed mart output in Postgres."""
    partition_date = bundled_stack_harness.default_partition_date()

    bundled_stack_harness.materialize(
        "dlt_posts",
        partition_date=partition_date,
        timeout=1200,
        stream_output=True,
    )
    bundled_stack_harness.materialize(
        "posts_mart",
        partition_date=partition_date,
        timeout=1200,
        stream_output=True,
    )

    trino = TrinoResource(
        host="127.0.0.1",
        port=bundled_stack_harness.ports.trino,
        catalog="iceberg",
    )
    mart_rows = trino.execute("SELECT count(*) FROM posts_mart", schema="raw_marts")
    mart_count = mart_rows[0][0] if mart_rows else 0
    assert mart_count > 0

    bundled_stack_harness.materialize(
        "publish_jsonplaceholder_marts",
        timeout=1200,
        stream_output=True,
    )

    env_vars = bundled_stack_harness.read_env()
    mart_schema = env_vars.get("POSTGRES_MART_SCHEMA", "marts")
    connection = psycopg2.connect(
        host="127.0.0.1",
        port=bundled_stack_harness.ports.postgres,
        user=env_vars.get("POSTGRES_USER", "phlo"),
        password=env_vars.get("POSTGRES_PASSWORD", "phlo"),
        dbname=env_vars.get("POSTGRES_DB", "phlo"),
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("SELECT count(*) FROM {}.{}").format(
                    sql.Identifier(mart_schema),
                    sql.Identifier("posts_mart"),
                )
            )
            published_count = cursor.fetchone()[0]
    finally:
        connection.close()

    assert published_count == mart_count
