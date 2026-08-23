"""SQL helper utilities for Trino API query construction and validation.

Provides SQL parsing, validation, and identifier quoting utilities
to ensure safe query construction for the Trino query endpoint.

Key Functions:
    quote_identifier: Safely quote SQL identifiers.
    qualify_table_name: Build fully qualified table names.
    is_probably_qualified_table: Check if table name is qualified.
    sql_literal: Convert Python values to SQL literals.
    validate_read_only_query: Validate queries for read-only mode.

Example:
    Building a safe query:

    .. code-block:: python

        from phlo_api.observatory_api.trino_sql import (
            quote_identifier, qualify_table_name, sql_literal
        )

        table = qualify_table_name("warehouse", "main", "events")
        query = f"SELECT * FROM {table} WHERE id = {sql_literal(123)}"

"""

from __future__ import annotations

import re
from math import isfinite

_FORBIDDEN_READ_ONLY_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "CREATE",
    "ALTER",
    "TRUNCATE",
    "MERGE",
    "CALL",
    "GRANT",
    "REVOKE",
)
_FORBIDDEN_READ_ONLY_PATTERN = re.compile(rf"\b({'|'.join(_FORBIDDEN_READ_ONLY_KEYWORDS)})\b")


def quote_identifier(identifier: str) -> str:
    """Quote an SQL identifier safely for Trino.

    Raises: ValueError if identifier is empty or contains NUL bytes.
    """
    if not identifier:
        raise ValueError("Identifier cannot be empty")
    if "\x00" in identifier:
        raise ValueError("Identifier cannot contain NUL bytes")
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def qualify_table_name(catalog: str, schema: str, table: str) -> str:
    """Build a fully qualified table name with proper quoting.

    Raises: ValueError if any identifier is invalid.
    """
    return f"{quote_identifier(catalog)}.{quote_identifier(schema)}.{quote_identifier(table)}"


def is_probably_qualified_table(table: str) -> bool:
    """Check if a table name appears to be fully qualified.

    No exceptions raised directly.
    """
    return table.count(".") >= 2 or table.startswith('"')


def sql_literal(value: object) -> str:
    """Convert a Python value to a safe SQL literal.

    Raises: ValueError if value is None, non-finite float, or unsupported type.
    """
    if value is None:
        raise ValueError("Use IS NULL for null filters")
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("Non-finite float values are not supported")
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    raise ValueError(f"Unsupported filter value type: {type(value).__name__}")


def strip_sql_literals_and_comments(query: str) -> str:
    """Return query with string literals, identifiers, and comments removed.

    This is used to prepare a query for keyword analysis by removing
    variable content that might contain forbidden keywords.

    No exceptions raised directly.
    """
    out: list[str] = []
    i = 0
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False
    length = len(query)
    # Elided characters become spaces rather than being dropped, so code on
    # either side of a literal or comment can never fuse into a token that
    # looks like (or hides) a forbidden keyword during the later scan.
    while i < length:
        ch = query[i]
        nxt = query[i + 1] if i + 1 < length else ""

        if in_line_comment:
            if ch in "\r\n":
                in_line_comment = False
                out.append(ch)
            else:
                out.append(" ")
            i += 1
            continue

        if in_block_comment:
            if ch == "*" and nxt == "/":
                out.extend([" ", " "])
                i += 2
                in_block_comment = False
                continue
            out.append(" ")
            i += 1
            continue

        if in_single:
            if ch == "'":
                if nxt == "'":
                    out.extend([" ", " "])
                    i += 2
                    continue
                in_single = False
            out.append(" ")
            i += 1
            continue

        if in_double:
            if ch == '"':
                if nxt == '"':
                    out.extend([" ", " "])
                    i += 2
                    continue
                in_double = False
            out.append(" ")
            i += 1
            continue

        if ch == "-" and nxt == "-":
            in_line_comment = True
            out.extend([" ", " "])
            i += 2
            continue

        if ch == "/" and nxt == "*":
            in_block_comment = True
            out.extend([" ", " "])
            i += 2
            continue

        if ch == "'":
            in_single = True
            out.append(" ")
            i += 1
            continue

        if ch == '"':
            in_double = True
            out.append(" ")
            i += 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def validate_read_only_query(query: str) -> str | None:
    """Validate a query is read-only and a single statement.

    Checks for forbidden keywords (INSERT, UPDATE, DELETE, etc.) and
    ensures only a single statement is present.

    No exceptions raised directly.
    """
    cleaned = strip_sql_literals_and_comments(query)
    trimmed = cleaned.strip()
    if not trimmed:
        return "Query cannot be empty"

    while trimmed.endswith(";"):
        trimmed = trimmed[:-1].rstrip()
    if ";" in trimmed:
        return "Multiple statements are not allowed in read-only mode"

    match = _FORBIDDEN_READ_ONLY_PATTERN.search(trimmed.upper())
    if match:
        return f"{match.group(1)} statements are not allowed in read-only mode"

    return None
