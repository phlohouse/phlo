"""Validation for ref-scoped, read-only query workspace statements."""

from __future__ import annotations

from sqlglot import exp, parse
from sqlglot.errors import ParseError


class InvalidWorkspaceQuery(ValueError):
    """The submitted SQL is not one read-only query for the selected catalog."""


def validate_workspace_query(sql: str, catalog: str) -> str:
    """Return normalized SQL after enforcing a single read-only, ref-scoped query."""
    if not sql.strip() or len(sql.encode("utf-8")) > 64 * 1024:
        raise InvalidWorkspaceQuery("Query must be non-empty and at most 64 KiB.")
    try:
        statements = [statement for statement in parse(sql, read="trino") if statement is not None]
    except ParseError as exc:
        raise InvalidWorkspaceQuery("SQL could not be parsed.") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise InvalidWorkspaceQuery("Only one read-only SQL query is allowed.")
    for table in statements[0].find_all(exp.Table):
        reference = table.args.get("catalog")
        if reference is not None and reference.name != catalog:
            raise InvalidWorkspaceQuery(
                "Query references a catalog outside the selected environment."
            )
    return statements[0].sql(dialect="trino")


def bound_workspace_query(sql: str, row_limit: int) -> str:
    """Wrap a validated query in a server-enforced output row limit."""
    if not 1 <= row_limit <= 100:
        raise ValueError("Query row limit is outside the supported range.")
    return f'SELECT * FROM ({sql}) AS "_phlo_query_result" LIMIT {row_limit + 1}'
