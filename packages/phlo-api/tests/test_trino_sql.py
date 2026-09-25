"""Pin Trino identifier quoting and table qualification contracts."""

from __future__ import annotations

import pytest

from phlo_api.observatory_api.trino import (
    qualify_table_name,
    quote_identifier,
    quote_qualified_table,
)


def test_quote_identifier_escapes_double_quotes() -> None:
    assert quote_identifier('col"umn') == '"col""umn"'


@pytest.mark.parametrize(
    ("identifier", "error"),
    [("", "Identifier cannot be empty"), ("abc\x00def", "Identifier cannot contain NUL bytes")],
)
def test_quote_identifier_rejects_invalid_identifiers(identifier: str, error: str) -> None:
    with pytest.raises(ValueError) as exc_info:
        quote_identifier(identifier)
    assert str(exc_info.value) == error


def test_qualify_table_name_quotes_all_parts() -> None:
    assert qualify_table_name("iceberg", "main", "events") == '"iceberg"."main"."events"'


@pytest.mark.parametrize(
    ("table_name", "expected"),
    [
        ("iceberg.main.events", '"iceberg"."main"."events"'),
        ('"ice.berg"."ma""in"."events"', '"ice.berg"."ma""in"."events"'),
    ],
)
def test_quote_qualified_table(table_name: str, expected: str) -> None:
    assert quote_qualified_table(table_name) == expected


@pytest.mark.parametrize(
    "table_name",
    [
        "catalog.schema.table; DROP TABLE users",
        '"a"."b"."c" --',
        "catalog.schema",
        "catalog.schema.table.more",
        '"unterminated.schema.table',
        '"a"b.c.d',
        "a..c",
    ],
)
def test_quote_qualified_table_rejects_sql_fragments(table_name: str) -> None:
    with pytest.raises(ValueError):
        quote_qualified_table(table_name)
