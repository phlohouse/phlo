"""Small SQL construction and safety helpers.

Identifiers are validated or quoted before rendering and literals are
conservatively escaped; ``validate_read_only_sql`` rejects any statement
containing a mutating keyword so helper-generated SQL stays read-only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from phlo.exceptions import PhloConfigError
from phlo.helpers.partitions import PartitionScope

_DANGEROUS_SQL = re.compile(
    r"\b(insert|update|delete|drop|create|alter|truncate|merge|grant|revoke)\b",
    re.IGNORECASE,
)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SYNTHETIC_KEY_DIALECTS = {"oracle", "sqlserver"}


def quote_identifier(identifier: str, *, quote_char: str = '"') -> str:
    """Quote one SQL identifier part."""
    escaped = identifier.replace(quote_char, quote_char * 2)
    return f"{quote_char}{escaped}{quote_char}"


def table_ref_sql(*parts: str, quote_parts: bool = False) -> str:
    """Render a table reference from catalog/schema/table parts."""
    clean = [part for part in parts if part]
    if quote_parts:
        return ".".join(quote_identifier(part) for part in clean)
    for part in clean:
        if not all(_IDENTIFIER.match(segment) for segment in part.split(".")):
            raise PhloConfigError(
                message=f"Unsafe SQL identifier: {part}",
                suggestions=["Pass quote_parts=True or use simple identifier names."],
            )
    return ".".join(clean)


def literal(value: Any) -> str:
    """Render a conservative SQL literal for helper-generated predicates."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return str(value)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def where_and(*predicates: str | None) -> str:
    """Join optional predicates into a WHERE clause body."""
    return " AND ".join(f"({predicate})" for predicate in predicates if predicate)


def limit_sql(sql: str, *, limit: int | None) -> str:
    """Apply a LIMIT clause when one is not already present."""
    stripped = sql.strip().rstrip(";")
    if limit is None:
        return stripped
    if re.search(r"\blimit\s+\d+\s*$", stripped, flags=re.IGNORECASE):
        return stripped
    return f"{stripped} LIMIT {int(limit)}"


def synthetic_key(
    *,
    dialect: str,
    fields: Iterable[str],
    namespace: str | None = None,
) -> str:
    """Render a deterministic SHA-256 synthetic row-key SQL expression.

    The rendered expression is part of Phlo's compatibility contract: for a
    given supported dialect, namespace, field order, and field values, Phlo will
    preserve the canonical payload semantics across releases. Each field is
    tagged as null or value, non-null values are cast with dialect-stable string
    casts, and values are length-prefixed before hashing so adjacent fields
    cannot collide by concatenation.
    """
    normalized_dialect = dialect.lower()
    if normalized_dialect not in _SYNTHETIC_KEY_DIALECTS:
        raise PhloConfigError(
            message=f"Unsupported synthetic key SQL dialect: {dialect}",
            suggestions=["Use dialect='oracle' or dialect='sqlserver'."],
        )

    normalized_fields = tuple(fields)
    if not normalized_fields:
        raise PhloConfigError(
            message="Synthetic key fields cannot be empty",
            suggestions=["Pass one or more source column names in field order."],
        )
    for field in normalized_fields:
        _validate_synthetic_key_identifier(field, label="field")

    parts: list[str] = []
    if namespace:
        parts.append(literal(_length_prefixed_namespace(namespace)))
    parts.extend(
        _synthetic_key_field_part(normalized_dialect, field) for field in normalized_fields
    )

    if normalized_dialect == "oracle":
        return f"STANDARD_HASH({' || '.join(parts)}, 'SHA256')"
    payload = f"CONCAT({', '.join(parts)})"
    return f"CONVERT(varchar(64), HASHBYTES('SHA2_256', {payload}), 2)"


def _synthetic_key_field_part(dialect: str, field: str) -> str:
    value = _synthetic_key_string_cast(dialect, field)
    length = _synthetic_key_length(dialect, value)
    if dialect == "oracle":
        return (
            f"(CASE WHEN {field} IS NULL THEN 'N:' ELSE 'V:' || "
            f"TO_CHAR({length}) || ':' || {value} END)"
        )
    return (
        f"(CASE WHEN {field} IS NULL THEN 'N:' ELSE CONCAT('V:', "
        f"CAST({length} AS varchar(20)), ':', {value}) END)"
    )


def _synthetic_key_string_cast(dialect: str, field: str) -> str:
    if dialect == "oracle":
        return f"CAST({field} AS VARCHAR2(4000))"
    return f"CAST({field} AS nvarchar(max))"


def _synthetic_key_length(dialect: str, value: str) -> str:
    if dialect == "oracle":
        return f"LENGTH({value})"
    return f"LEN({value})"


def _length_prefixed_namespace(namespace: str) -> str:
    return f"NS:{len(namespace)}:{namespace}"


def _validate_synthetic_key_identifier(identifier: str, *, label: str) -> None:
    if not _IDENTIFIER.match(identifier):
        raise PhloConfigError(
            message=f"Unsafe synthetic key {label}: {identifier}",
            suggestions=["Use simple unquoted column names such as source_id or customer_id."],
        )


def render_partition_predicate(scope: PartitionScope) -> str | None:
    """Render a SQL predicate for a partition scope."""
    if scope.full_table:
        return None
    column = table_ref_sql(scope.partition_column)
    if scope.partition_key:
        return f"{column} = {literal(scope.partition_key)}"
    predicates: list[str] = []
    if scope.start:
        predicates.append(f"{column} >= {literal(scope.start)}")
    if scope.end:
        predicates.append(f"{column} <= {literal(scope.end)}")
    if scope.rolling_window_days:
        start = (datetime.now(UTC).date() - timedelta(days=scope.rolling_window_days)).isoformat()
        predicates.append(f"{column} >= {literal(start)}")
    return where_and(*predicates) or None


def partition_where_clause(scope: PartitionScope) -> str:
    """Render a full WHERE clause for a partition scope."""
    predicate = render_partition_predicate(scope)
    return f"WHERE {predicate}" if predicate else ""


def apply_where(sql: str, *predicates: str | None) -> str:
    """Append helper predicates to a SELECT query."""
    predicate = where_and(*predicates)
    stripped = sql.strip().rstrip(";")
    if not predicate:
        return stripped
    keyword = " AND " if re.search(r"\bwhere\b", stripped, re.IGNORECASE) else " WHERE "
    return f"{stripped}{keyword}{predicate}"


def validate_read_only_sql(sql: str) -> str:
    """Validate that SQL is a single read-only statement."""
    stripped = sql.strip().rstrip(";")
    if not stripped:
        raise PhloConfigError(message="SQL query cannot be empty")
    if ";" in stripped:
        raise PhloConfigError(
            message="SQL query must contain a single statement",
            suggestions=["Remove additional statements and rerun the query."],
        )
    if _DANGEROUS_SQL.search(stripped):
        raise PhloConfigError(
            message="SQL query must be read-only",
            suggestions=["Use SELECT/SHOW/DESCRIBE queries in helper read paths."],
        )
    return stripped


def select_columns(table: str, columns: Iterable[str] = ("*",), *, limit: int | None = None) -> str:
    """Build a simple read-only SELECT statement."""
    rendered_columns = ", ".join(columns)
    return limit_sql(f"SELECT {rendered_columns} FROM {table_ref_sql(table)}", limit=limit)
