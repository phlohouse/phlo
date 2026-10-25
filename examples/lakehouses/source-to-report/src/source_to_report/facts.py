"""Strict lookups for the report's numbers.

The report template never computes a number and never reads a file. It asks
this object for a row or a summary value. A missing table, column or row
stops the build, so the prose cannot quietly drift from the data.

Several figures in one row is also an error: a lookup that is ambiguous is a
question the report has not answered, not something to guess.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

_TABLE = re.compile(r"^[a-z_][a-z0-9_]*$")
_MISSING = object()


class FactError(LookupError):
    """A figure the report needs is not in the run's output."""


def _coerce(value: Any) -> Any:
    """Turn a CSV cell into an int or float when it is one, else leave it alone."""
    if not isinstance(value, str) or not value.strip():
        return value
    if "." not in value and "e" not in value.lower():
        try:
            return int(value)
        except ValueError:
            pass
    try:
        return float(value)
    except ValueError:
        return value


class Facts:
    """Tables and summary values written by one analysis run."""

    def __init__(self, data_dir: Path, summary: dict[str, Any]) -> None:
        self._data_dir = data_dir
        self._summary = summary
        self._tables: dict[str, list[dict[str, Any]]] = {}

    def rows(self, table: str) -> list[dict[str, Any]]:
        """Return every row of a table."""
        if not _TABLE.match(table):
            raise FactError(f"invalid table name: {table!r}")
        if table not in self._tables:
            path = self._data_dir / f"{table}.csv"
            if not path.is_file():
                raise FactError(f"missing table: {table} ({path})")
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames is None:
                    raise FactError(f"table {table} has no header row")
                self._tables[table] = [
                    {key: _coerce(value) for key, value in row.items() if key is not None}
                    for row in reader
                ]
        return self._tables[table]

    def row(self, table: str, **match: Any) -> dict[str, Any]:
        """Return the one row matching every keyword argument."""
        expected = {key: _coerce(value) for key, value in match.items()}
        found = [
            row
            for row in self.rows(table)
            if all(row.get(key) == value for key, value in expected.items())
        ]
        if not found:
            raise FactError(f"{table}: no row matches {match}")
        if len(found) > 1:
            raise FactError(f"{table}: {len(found)} rows match {match}; the lookup must be unique")
        return found[0]

    def value(self, table: str, column: str, **match: Any) -> Any:
        """Return one cell."""
        row = self.row(table, **match)
        if column not in row:
            raise FactError(f"{table}: no column {column!r}; columns are {sorted(row)}")
        return row[column]

    def extreme(
        self, table: str, column: str, *, mode: str = "max", **match: Any
    ) -> dict[str, Any]:
        """Return the row with the largest or smallest value in ``column``."""
        if mode not in {"max", "min"}:
            raise FactError(f"mode must be 'max' or 'min', not {mode!r}")
        expected = {key: _coerce(value) for key, value in match.items()}
        candidates = [
            row
            for row in self.rows(table)
            if all(row.get(key) == value for key, value in expected.items())
        ]
        if not candidates:
            raise FactError(f"{table}: no row matches {match}")
        try:
            return (max if mode == "max" else min)(candidates, key=lambda row: float(row[column]))
        except KeyError as error:
            raise FactError(f"{table}: no column {column!r}") from error

    def total(self, table: str, column: str, **match: Any) -> float:
        """Sum a column, optionally over matching rows only."""
        expected = {key: _coerce(value) for key, value in match.items()}
        total = 0.0
        found = False
        for row in self.rows(table):
            if not all(row.get(key) == value for key, value in expected.items()):
                continue
            if column not in row:
                raise FactError(f"{table}: no column {column!r}")
            total += float(row[column])
            found = True
        if not found:
            raise FactError(f"{table}: no rows match {match}")
        return total

    def summary(self, path: str) -> Any:
        """Return a dotted summary value, for example ``counts.readings``."""
        current: Any = self._summary
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                raise FactError(f"summary has no value at {path!r}")
            current = current[part]
        return current

    def get(self, path: str, default: Any = _MISSING) -> Any:
        """Return a dotted summary value, or ``default`` when it is absent."""
        try:
            return self.summary(path)
        except FactError:
            if default is _MISSING:
                raise
            return default

    def checks(self) -> list[dict[str, Any]]:
        """Return the checks the analysis recorded."""
        checks = self._summary.get("checks")
        if not isinstance(checks, list):
            raise FactError("summary has no checks list")
        return checks

    def check_ok(self, name: str) -> bool:
        """Return whether a named check passed; fail when the check does not exist."""
        for check in self.checks():
            if check.get("name") == name:
                return bool(check.get("ok"))
        raise FactError(f"summary has no check named {name!r}")


def load(out_dir: Path) -> Facts:
    """Load ``summary.json`` and the tables written under ``out_dir/data``."""
    path = out_dir / "summary.json"
    if not path.is_file():
        raise FactError(f"missing summary: {path}")
    summary = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(summary, dict):
        raise FactError(f"summary must be an object: {path}")
    return Facts(out_dir / "data", summary)
