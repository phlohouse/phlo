"""Test read-only query validation, catalog isolation, and result bounds."""

import pytest

from phlo_api.api.v1_query_sql import (
    InvalidWorkspaceQuery,
    bound_workspace_query,
    validate_workspace_query,
)


@pytest.mark.parametrize(
    "sql",
    [
        "select * from staging_catalog.analytics.orders",
        "with source as (select * from staging_catalog.analytics.orders) select * from source",
        "delete from prod_catalog.analytics.orders",
        "select 1; select 2",
        "create table prod_catalog.analytics.copy as select * from prod_catalog.analytics.orders",
        "SELECT * FROM TABLE(system.query(query => 'DELETE FROM orders'))",
        "WITH deleted AS (DELETE FROM orders RETURNING *) SELECT * FROM deleted",
        "SELECT * INTO copy FROM orders",
    ],
)
def test_query_rejects_writes_or_references_outside_selected_catalog(sql: str) -> None:
    with pytest.raises(InvalidWorkspaceQuery):
        validate_workspace_query(sql, "prod_catalog")


def test_query_accepts_ctes_joins_and_comments_inside_selected_catalog() -> None:
    result = validate_workspace_query(
        "-- read only\nWITH orders AS (SELECT id FROM prod_catalog.analytics.orders) "
        "SELECT * FROM orders JOIN prod_catalog.analytics.customers USING (id)",
        "prod_catalog",
    )

    assert "WITH orders AS" in result
    assert "prod_catalog.analytics.orders" in result


def test_query_rejects_empty_and_oversized_sql() -> None:
    for sql in ("  ", "x" * (64 * 1024 + 1)):
        with pytest.raises(InvalidWorkspaceQuery):
            validate_workspace_query(sql, "prod_catalog")


def test_query_limit_is_applied_by_the_query_engine() -> None:
    bounded = bound_workspace_query("SELECT id FROM orders ORDER BY id", 8)

    assert bounded == "SELECT id FROM orders ORDER BY id LIMIT 9"


@pytest.mark.parametrize("requested,expected", [(0, 0), (3, 3), (500, 9)])
def test_output_bound_preserves_smaller_limits_and_offsets(requested: int, expected: int) -> None:
    assert (
        bound_workspace_query(f"SELECT id FROM orders ORDER BY id LIMIT {requested} OFFSET 2", 8)
        == f"SELECT id FROM orders ORDER BY id OFFSET 2 LIMIT {expected}"
    )


def test_output_bound_keeps_union_order_at_the_top_level() -> None:
    assert bound_workspace_query("SELECT 2 AS id UNION ALL SELECT 1 ORDER BY id", 8) == (
        "SELECT 2 AS id UNION ALL SELECT 1 ORDER BY id LIMIT 9"
    )


def test_output_bound_preserves_ordered_fetch_and_top_level_parentheses() -> None:
    for sql in (
        "SELECT id FROM orders ORDER BY id FETCH FIRST 2 ROWS ONLY",
        "(SELECT id FROM orders ORDER BY id LIMIT 2)",
    ):
        assert bound_workspace_query(sql, 8) == "SELECT id FROM orders ORDER BY id LIMIT 2"


def test_output_bound_preserves_fetch_offset_and_caps_limit_all() -> None:
    assert (
        bound_workspace_query(
            "SELECT id FROM orders ORDER BY id OFFSET 3 ROWS FETCH FIRST 2 ROWS ONLY", 8
        )
        == "SELECT id FROM orders ORDER BY id OFFSET 3 LIMIT 2"
    )
    assert bound_workspace_query("SELECT id FROM orders ORDER BY id LIMIT ALL", 8) == (
        "SELECT id FROM orders ORDER BY id LIMIT 9"
    )


def test_preview_rejects_modifiers_outside_parentheses_without_dropping_them() -> None:
    sql = "(SELECT id FROM orders ORDER BY id LIMIT 2) LIMIT 1"
    for operation in (
        lambda: validate_workspace_query(sql, "prod_catalog"),
        lambda: bound_workspace_query(sql, 8),
    ):
        with pytest.raises(InvalidWorkspaceQuery, match="outer parentheses"):
            operation()


@pytest.mark.parametrize("suffix", ["LIMIT 5 PERCENT", "FETCH FIRST 2 ROWS WITH TIES"])
def test_preview_rejects_limits_that_cannot_be_capped_without_changing_meaning(suffix: str) -> None:
    sql = f"SELECT id FROM orders ORDER BY id {suffix}"
    with pytest.raises(InvalidWorkspaceQuery, match="not supported by bounded preview"):
        validate_workspace_query(sql, "prod_catalog")
    with pytest.raises(InvalidWorkspaceQuery, match="not supported by bounded preview"):
        bound_workspace_query(sql, 8)
