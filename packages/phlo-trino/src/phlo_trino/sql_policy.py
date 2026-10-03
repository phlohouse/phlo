"""Main-write protection at the Trino statement submission boundary."""

from __future__ import annotations

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from phlo.cli.sql import first_sql_verb
from phlo.plugins.observatory_settings import get_operational_settings


def require_governed_sql(sql: str, *, ref: str | None, catalog: str | None) -> None:
    """Allow reads and provably branch-scoped writes when main is protected."""
    if not get_operational_settings().protect_main:
        return
    try:
        statements = [item for item in parse(sql, read="trino") if item is not None]
    except ParseError as exc:
        raise PermissionError(
            "Protected SQL must have a verifiable read or branch-write intent."
        ) from exc
    if len(statements) != 1:
        raise PermissionError("Protected SQL requires exactly one statement.")
    statement = statements[0]
    tables = list(statement.find_all(exp.Table))
    if any(not isinstance(table.this, exp.Identifier) for table in tables):
        raise PermissionError(
            "Connector passthrough and table functions cannot prove main-write protection."
        )
    if isinstance(statement, exp.Query) and not statement.find(exp.DML):
        return
    if isinstance(statement, exp.Command) and first_sql_verb(sql) in {"SHOW", "DESCRIBE", "DESC"}:
        return
    if not ref or ref == "main":
        raise PermissionError(
            "Direct SQL writes to main are protected; write to a branch and use a signed merge."
        )
    if (
        not isinstance(
            statement,
            (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter),
        )
        or not tables
        or not catalog
        or any(table.catalog and table.catalog != catalog for table in tables)
    ):
        raise PermissionError(
            "SQL mutation does not prove the selected branch catalog; use a branch-scoped table operation."
        )
