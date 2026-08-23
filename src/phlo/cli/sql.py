"""Small SQL helpers for CLI surfaces.

Classification is conservative: literals, quoted identifiers, and
comments are blanked (preserving offsets) before verb extraction so a
mutating verb inside a string can never trigger the mutating verdict,
and only an explicit mutating verb counts as unsafe.
"""

from __future__ import annotations

import re

_LEADING_COMMENT_PATTERN = re.compile(
    r"""
    \A
    (?:
      \s+
      | --[^\n]*(?:\n|$)
      | /\*.*?\*/
    )+
    """,
    re.DOTALL | re.VERBOSE,
)

_MUTATING_SQL_VERBS = frozenset(
    {
        "ALTER",
        "ATTACH",
        "CALL",
        "CREATE",
        "DELETE",
        "DETACH",
        "DROP",
        "EXCHANGE",
        "EXECUTE",
        "GRANT",
        "INSERT",
        "KILL",
        "MERGE",
        "OPTIMIZE",
        "RENAME",
        "REPLACE",
        "REVOKE",
        "SYSTEM",
        "TRUNCATE",
        "UPDATE",
    }
)
_MUTATING_SQL_PATTERN = re.compile(rf"\b({'|'.join(sorted(_MUTATING_SQL_VERBS))})\b")


def strip_sql_literals_and_comments(sql: str) -> str:
    """Return SQL with string literals, quoted identifiers, and comments blanked."""
    out: list[str] = []
    i = 0
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False
    length = len(sql)

    while i < length:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < length else ""

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
                in_block_comment = False
                i += 2
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


def _first_sql_verb_from_cleaned(sql: str) -> str:
    stripped = _LEADING_COMMENT_PATTERN.sub("", sql)
    match = re.match(r"([A-Za-z_]+)", stripped)
    return match.group(1).upper() if match else ""


def first_sql_verb(sql: str) -> str:
    """Return the leading SQL verb after whitespace and comments."""
    return _first_sql_verb_from_cleaned(strip_sql_literals_and_comments(sql))


def is_mutating_sql(sql: str) -> bool:
    """Return true when a SQL statement is clearly mutating state."""
    cleaned = strip_sql_literals_and_comments(sql)
    for statement in cleaned.split(";"):
        verb = _first_sql_verb_from_cleaned(statement)
        if verb in _MUTATING_SQL_VERBS:
            return True
        if verb == "WITH" and _MUTATING_SQL_PATTERN.search(statement.upper()):
            return True
    return False
