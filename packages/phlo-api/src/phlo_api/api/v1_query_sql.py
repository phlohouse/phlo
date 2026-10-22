"""Validation for ref-scoped, read-only query workspace statements."""

from __future__ import annotations

from sqlglot import exp, parse
from sqlglot.errors import ParseError


class InvalidWorkspaceQuery(ValueError):
    """The submitted SQL is not one read-only query for the selected catalog."""


def _unparenthesized(statement: exp.Query) -> exp.Query:
    while isinstance(statement, exp.Subquery) and isinstance(statement.this, exp.Query):
        if any(
            statement.args.get(key) is not None for key in ("limit", "offset", "order", "with_")
        ):
            raise InvalidWorkspaceQuery(
                "Place ORDER BY, OFFSET and LIMIT inside the query's outer parentheses."
            )
        statement = statement.this
    return statement


def _limit_count(statement: exp.Query) -> int | None:
    limit = statement.args.get("limit")
    if limit is None:
        return None
    options = limit.args.get("limit_options")
    if options is not None and (options.args.get("percent") or options.args.get("with_ties")):
        raise InvalidWorkspaceQuery(
            "Percentage limits and WITH TIES are not supported by bounded preview. "
            "Use ORDER BY and a numeric LIMIT."
        )
    count = limit.args.get("count") if isinstance(limit, exp.Fetch) else limit.expression
    if count is None and isinstance(limit, exp.Fetch):
        return 1
    if isinstance(count, exp.Literal) and count.is_int and int(count.this) >= 0:
        return int(count.this)
    if isinstance(count, exp.Column) and count.name.upper() == "ALL":
        return None
    raise InvalidWorkspaceQuery("Bounded preview requires a non-negative numeric LIMIT.")


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
    statements[0] = _unparenthesized(statements[0])
    if any(
        isinstance(node, (exp.DDL, exp.DML, exp.Command, exp.Into)) for node in statements[0].walk()
    ):
        raise InvalidWorkspaceQuery(
            "SQL mutations are not allowed, including inside query expressions."
        )
    for table in statements[0].find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise InvalidWorkspaceQuery(
                "Connector passthrough and table functions are not allowed."
            )
        reference = table.args.get("catalog")
        if reference is not None and reference.name != catalog:
            raise InvalidWorkspaceQuery(
                "Query references a catalog outside the selected environment."
            )
    _limit_count(statements[0])
    return statements[0].sql(dialect="trino")


def bound_workspace_query(sql: str, row_limit: int) -> str:
    """Bound validated output without making top-level ordering redundant."""
    if not 1 <= row_limit <= 100:
        raise ValueError("Query row limit is outside the supported range.")
    statement = parse(sql, read="trino")[0]
    if not isinstance(statement, exp.Query):
        raise InvalidWorkspaceQuery("Only one read-only SQL query is allowed.")
    statement = _unparenthesized(statement)
    count = _limit_count(statement)
    maximum = row_limit + 1
    return statement.limit(maximum if count is None else min(maximum, count)).sql(dialect="trino")
