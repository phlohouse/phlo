"""Unit tests for TrinoResource DataFrame read helpers.

Drives reads through a fake cursor to pin parameterised SQL execution, cursor
lifecycle, and error propagation.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import pandas as pd
import pytest

from phlo.references import LogicalRelation
from phlo_trino import TrinoResource


class FakeCursor:
    def __init__(
        self,
        *,
        description: list[tuple[str]] | None = None,
        rows: list[tuple[object, ...]] | None = None,
        error: Exception | None = None,
        fetch_error: Exception | None = None,
    ) -> None:
        self.description = description
        self.rows = rows or []
        self.error = error
        self.fetch_error = fetch_error
        self.executed: tuple[str, list[object]] | None = None
        self.closed = False

    def execute(self, sql: str, params: list[object]) -> None:
        self.executed = (sql, params)
        if self.error is not None:
            raise self.error

    def fetchall(self) -> list[tuple[object, ...]]:
        if self.fetch_error is not None:
            raise self.fetch_error
        return self.rows

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeConnection:
    fake_cursor: FakeCursor
    closed: bool = False
    cursor_error: Exception | None = None

    def cursor(self) -> FakeCursor:
        if self.cursor_error is not None:
            raise self.cursor_error
        return self.fake_cursor

    def close(self) -> None:
        self.closed = True


def test_read_dataframe_returns_pandas_dataframe_and_forwards_params() -> None:
    cursor = FakeCursor(
        description=[("id",), ("name",)],
        rows=[(1, "alpha"), (2, "beta")],
    )
    connection = FakeConnection(cursor)
    resource = TrinoResource(host="test", port=8080)

    with patch.object(resource, "get_connection", return_value=connection):
        result = resource.read_dataframe("SELECT id, name FROM raw.events WHERE id > ?", [1])

    assert result.equals(pd.DataFrame({"id": [1, 2], "name": ["alpha", "beta"]}))
    assert cursor.executed == ("SELECT id, name FROM raw.events WHERE id > ?", [1])
    assert cursor.closed is True
    assert connection.closed is True


def test_read_dataframe_accepts_logical_relation() -> None:
    cursor = FakeCursor(description=[("id",)], rows=[(1,)])
    connection = FakeConnection(cursor)
    relation = LogicalRelation(
        asset_key="orders",
        catalog="iceberg",
        schema="gold",
        table='order"facts',
    )
    resource = TrinoResource(host="test", port=8080)

    with patch.object(resource, "get_connection", return_value=connection):
        result = resource.read_dataframe(relation)

    assert result.to_dict(orient="records") == [{"id": 1}]
    assert cursor.executed == (
        'SELECT * FROM "iceberg"."gold"."order""facts"',
        [],
    )


def test_read_table_renders_columns_and_limit_for_relation() -> None:
    cursor = FakeCursor(description=[("order_id",)], rows=[(1,)])
    connection = FakeConnection(cursor)
    relation = LogicalRelation(asset_key="orders", schema="gold", table="orders")
    resource = TrinoResource(host="test", port=8080)

    with patch.object(resource, "get_connection", return_value=connection):
        result = resource.read_table(relation, columns=["order_id"], limit=10)

    assert result.to_dict(orient="records") == [{"order_id": 1}]
    assert cursor.executed == ('SELECT "order_id" FROM "gold"."orders" LIMIT 10', [])


def test_read_table_quotes_string_table_identifiers() -> None:
    cursor = FakeCursor(description=[("order id",)], rows=[(1,)])
    connection = FakeConnection(cursor)
    resource = TrinoResource(host="test", port=8080)

    with patch.object(resource, "get_connection", return_value=connection):
        result = resource.read_table("gold.order facts", columns=["order id"])

    assert result.to_dict(orient="records") == [{"order id": 1}]
    assert cursor.executed == ('SELECT "order id" FROM "gold"."order facts"', [])


def test_read_table_rejects_negative_limit_before_opening_connection() -> None:
    resource = TrinoResource(host="test", port=8080)

    with patch.object(resource, "get_connection") as get_connection:
        with pytest.raises(ValueError, match="limit must be non-negative"):
            resource.read_table("gold.orders", limit=-1)

    get_connection.assert_not_called()


def test_read_dataframe_returns_empty_dataframe_for_statements_without_results() -> None:
    cursor = FakeCursor(description=None)
    connection = FakeConnection(cursor)
    resource = TrinoResource(host="test", port=8080)

    with patch.object(resource, "get_connection", return_value=connection):
        result = resource.read_dataframe("CREATE TABLE raw.events (id INT)")

    assert result.empty
    assert list(result.columns) == []


def test_read_dataframe_forwards_connection_schema() -> None:
    cursor = FakeCursor(description=[("id",)], rows=[(1,)])
    connection = FakeConnection(cursor)
    resource = TrinoResource(host="test", port=8080)

    with patch.object(resource, "get_connection", return_value=connection) as get_connection:
        resource.read_dataframe("SELECT id FROM events", schema="raw")

    get_connection.assert_called_once_with(schema="raw")


def test_read_dataframe_closes_resources_and_adds_query_context_on_error() -> None:
    cursor = FakeCursor(error=ValueError("syntax error near FROM"))
    connection = FakeConnection(cursor)
    resource = TrinoResource(host="test", port=8080)

    with patch.object(resource, "get_connection", return_value=connection):
        with pytest.raises(RuntimeError, match="Trino query failed") as exc_info:
            resource.read_dataframe("SELECT * FROM")

    assert "SELECT * FROM" in str(exc_info.value)
    assert "syntax error near FROM" in str(exc_info.value)
    assert cursor.closed is True
    assert connection.closed is True


def test_read_dataframe_redacts_string_literals_in_error_context() -> None:
    cursor = FakeCursor(error=ValueError("permission denied"))
    connection = FakeConnection(cursor)
    resource = TrinoResource(host="test", port=8080)

    with patch.object(resource, "get_connection", return_value=connection):
        with pytest.raises(RuntimeError) as exc_info:
            resource.read_dataframe("SELECT * FROM raw.events WHERE token = 'secret-token'")

    message = str(exc_info.value)
    assert "secret-token" not in message
    assert "token = '?'" in message


def test_read_dataframe_closes_connection_when_cursor_creation_fails() -> None:
    cursor = FakeCursor(description=[("id",)], rows=[(1,)])
    connection = FakeConnection(cursor, cursor_error=RuntimeError("cursor failed"))
    resource = TrinoResource(host="test", port=8080)

    with patch.object(resource, "get_connection", return_value=connection):
        with pytest.raises(RuntimeError, match="cursor failed"):
            resource.read_dataframe("SELECT id FROM raw.events")

    assert cursor.closed is False
    assert connection.closed is True


def test_read_dataframe_closes_resources_and_adds_context_on_fetch_error() -> None:
    cursor = FakeCursor(
        description=[("id",)],
        fetch_error=ValueError("network interrupted"),
    )
    connection = FakeConnection(cursor)
    resource = TrinoResource(host="test", port=8080)

    with patch.object(resource, "get_connection", return_value=connection):
        with pytest.raises(RuntimeError, match="Trino query failed") as exc_info:
            resource.read_dataframe("SELECT id FROM raw.events")

    assert "SELECT id FROM raw.events" in str(exc_info.value)
    assert "network interrupted" in str(exc_info.value)
    assert cursor.closed is True
    assert connection.closed is True


def test_read_dataframe_applies_optional_schema_types() -> None:
    cursor = FakeCursor(description=[("id",), ("name",)], rows=[("1", 2)])
    connection = FakeConnection(cursor)

    class Schema:
        id: int
        name: str

    resource = TrinoResource(host="test", port=8080)

    with patch.object(resource, "get_connection", return_value=connection):
        result = resource.read_dataframe("SELECT id, name FROM raw.events", schema_class=Schema)

    assert result["id"].dtype.name in ("Int64", "int64")
    assert result["name"].dtype.name == "string"


def test_preview_uses_limit_plus_one_and_normalizes_rows() -> None:
    cursor = FakeCursor(
        description=[("id", "bigint"), ("name", "varchar")],
        rows=[(1, "one"), (2, "two"), (3, "three")],
    )
    connection = FakeConnection(cursor)
    resource = TrinoResource(host="test", port=8080)

    with patch.object(resource, "get_connection", return_value=connection):
        result = resource.preview('"iceberg"."raw"."events"', limit=2, offset=4, schema="raw")

    assert cursor.executed == ('SELECT * FROM "iceberg"."raw"."events" OFFSET 4 LIMIT 3', [])
    assert result.rows == [{"id": 1, "name": "one"}, {"id": 2, "name": "two"}]
    assert result.has_more is True
