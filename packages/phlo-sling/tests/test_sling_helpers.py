"""Tests for ergonomic Sling helper utilities.

Pins partition WHERE-clause construction with SQL escaping, stream-to-table-name
sanitising, and replication-plan defaults.
"""

from __future__ import annotations

import json

from phlo_sling.helpers import (
    ConnectionSummary,
    ReplicationPlan,
    build_partition_where,
    build_replication_plan,
    summarize_connections,
    table_name_from_stream,
)
from phlo_sling.registry import SlingReplication


def test_build_partition_where_uses_half_open_window_by_default() -> None:
    assert build_partition_where("updated_at", "2026-01-01", "2026-02-01") == (
        "updated_at >= '2026-01-01' AND updated_at < '2026-02-01'"
    )


def test_build_partition_where_can_include_upper_bound_and_escape_values() -> None:
    assert build_partition_where("event_date", "2026-01-01", "2026-01-31", inclusive_end=True) == (
        "event_date >= '2026-01-01' AND event_date <= '2026-01-31'"
    )
    assert build_partition_where("name", "O'Reilly") == "name >= 'O''Reilly'"


def test_table_name_from_stream_sanitizes_common_stream_shapes() -> None:
    assert table_name_from_stream("public.orders") == "orders"
    assert table_name_from_stream("sales.order-items") == "order_items"
    assert table_name_from_stream('"Sales"."Order Items"') == "order_items"


def test_build_replication_plan_creates_sling_replications_with_defaults() -> None:
    plan = build_replication_plan(
        ["public.orders", {"stream_name": "sales.customers", "primary_key": "id"}],
        source_conn="PHLO_POSTGRES",
        target_conn="PHLO_ICEBERG",
        update_key="updated_at",
        group_name="raw",
        where="updated_at >= '2026-01-01'",
    )

    assert plan == ReplicationPlan(
        replications=[
            SlingReplication(
                stream_name="public.orders",
                table_name="orders",
                source_conn="PHLO_POSTGRES",
                target_conn="PHLO_ICEBERG",
                mode="incremental",
                primary_key=None,
                update_key="updated_at",
                group_name="raw",
                where="updated_at >= '2026-01-01'",
            ),
            SlingReplication(
                stream_name="sales.customers",
                table_name="customers",
                source_conn="PHLO_POSTGRES",
                target_conn="PHLO_ICEBERG",
                mode="incremental",
                primary_key="id",
                update_key="updated_at",
                group_name="raw",
                where="updated_at >= '2026-01-01'",
            ),
        ]
    )


def test_summarize_connections_redacts_secrets_and_reads_environment(monkeypatch) -> None:
    monkeypatch.setenv(
        "PHLO_POSTGRES",
        json.dumps({"type": "postgres", "host": "localhost", "password": "secret"}),
    )
    monkeypatch.setenv("UNRELATED_JSON", json.dumps({"type": "tool", "enabled": True}))
    monkeypatch.setattr(
        "phlo_sling.helpers.resolve_phlo_connections",
        lambda: {
            "PHLO_S3": {"type": "s3", "endpoint": "http://minio", "secret_access_key": "secret"}
        },
    )

    assert summarize_connections() == {
        "PHLO_POSTGRES": ConnectionSummary(
            name="PHLO_POSTGRES",
            type="postgres",
            configured=True,
            keys=["host", "password", "type"],
            redacted_keys=["password"],
        ),
        "PHLO_S3": ConnectionSummary(
            name="PHLO_S3",
            type="s3",
            configured=True,
            keys=["endpoint", "secret_access_key", "type"],
            redacted_keys=["secret_access_key"],
        ),
    }
