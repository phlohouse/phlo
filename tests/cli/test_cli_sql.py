"""Tests for SQL verb extraction and mutation classification.

Parametrized cases confirm the leading verb is found after comments and other
leading noise, and mutating statements classify consistently.
"""

from __future__ import annotations

import pytest

from phlo.cli.sql import first_sql_verb, is_mutating_sql


@pytest.mark.parametrize(
    ("sql", "verb"),
    [
        ("SELECT 1", "SELECT"),
        ("  -- explain why\nWITH rows AS (SELECT 1) SELECT * FROM rows", "WITH"),
        ("/* maintenance */ INSERT INTO t VALUES (1)", "INSERT"),
    ],
)
def test_first_sql_verb_skips_leading_comments(sql: str, verb: str) -> None:
    assert first_sql_verb(sql) == verb


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1", False),
        ("WITH rows AS (SELECT 1) SELECT * FROM rows", False),
        ("SHOW TABLES", False),
        ("SHOW CREATE TABLE orders", False),
        ("/* load */ INSERT INTO t VALUES (1)", True),
        ("SELECT 1; INSERT INTO t VALUES (1)", True),
        ("WITH source AS (SELECT 1) INSERT INTO t SELECT * FROM source", True),
        # A literal that merely mentions DDL is data, not a mutation.
        ("SELECT 'DROP TABLE t' AS text_col", False),
        ("DROP TABLE t", True),
    ],
)
def test_is_mutating_sql(sql: str, expected: bool) -> None:
    assert is_mutating_sql(sql) is expected
