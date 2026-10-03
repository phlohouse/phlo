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

    assert bounded == (
        'SELECT * FROM (SELECT id FROM orders ORDER BY id) AS "_phlo_query_result" LIMIT 9'
    )
