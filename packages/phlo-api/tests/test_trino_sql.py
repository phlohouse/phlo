"""Unit tests for Trino SQL helper utilities.

Pins identifier quoting, literal rendering, table qualification, and read-only
query validation contracts.
"""

from __future__ import annotations

import pytest

from phlo_api.observatory_api.trino_sql import (
    is_probably_qualified_table,
    qualify_table_name,
    quote_identifier,
    sql_literal,
    strip_sql_literals_and_comments,
    validate_read_only_query,
)


def test_quote_identifier_escapes_double_quotes() -> None:
    assert quote_identifier('col"umn') == '"col""umn"'


@pytest.mark.parametrize("identifier", ["", "abc\x00def"])
def test_quote_identifier_rejects_invalid_identifiers(identifier: str) -> None:
    with pytest.raises(ValueError):
        quote_identifier(identifier)


def test_qualify_table_name_quotes_all_parts() -> None:
    assert qualify_table_name("iceberg", "main", "events") == '"iceberg"."main"."events"'


@pytest.mark.parametrize(
    ("table_name", "expected"),
    [("iceberg.main.events", True), ('"iceberg"."main"."events"', True), ("events", False)],
)
def test_is_probably_qualified_table(table_name: str, expected: bool) -> None:
    assert is_probably_qualified_table(table_name) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, "TRUE"),
        (False, "FALSE"),
        (12, "12"),
        (2.5, "2.5"),
        ("O'Hare", "'O''Hare'"),
    ],
)
def test_sql_literal_supported_types(value: object, expected: str) -> None:
    assert sql_literal(value) == expected


@pytest.mark.parametrize("value", [None, float("inf"), float("-inf"), float("nan"), {"k": "v"}])
def test_sql_literal_rejects_unsafe_values(value: object) -> None:
    with pytest.raises(ValueError):
        sql_literal(value)


def test_strip_sql_literals_and_comments_removes_forbidden_tokens() -> None:
    query = """
    SELECT '-- INSERT in string' AS text_col, "DROP" AS identifier
    FROM events
    -- DELETE from comment
    WHERE note = 'UPDATE here' /* CREATE block */
    """
    stripped = strip_sql_literals_and_comments(query)
    assert "INSERT" not in stripped
    assert "DELETE" not in stripped
    assert "UPDATE" not in stripped
    assert "CREATE" not in stripped
    assert "SELECT" in stripped
    assert "FROM events" in stripped


@pytest.mark.parametrize(
    ("query", "expected_error"),
    [
        ("", "Query cannot be empty"),
        ("SELECT 1; SELECT 2", "Multiple statements are not allowed in read-only mode"),
        ("DELETE FROM events", "DELETE statements are not allowed in read-only mode"),
        ("SELECT 'DROP TABLE users' AS text", None),
        ("SELECT 1;", None),
    ],
)
def test_validate_read_only_query(query: str, expected_error: str | None) -> None:
    assert validate_read_only_query(query) == expected_error
