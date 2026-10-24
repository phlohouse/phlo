"""A small read-only SQL adapter for the analysis stage.

The analysis asks for named marts and gets dictionaries back. DuckDB is the
local path; the orchestrator passes its Trino engine, which exposes the same
cursor API. Table names are validated and quoted, so no caller can smuggle
SQL through a relation name.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


class Warehouse:
    """Read-only access to the built marts."""

    def __init__(
        self,
        *,
        duckdb_path: Path | None = None,
        engine: Any = None,
        catalog: str = "iceberg",
        schema: str = "analytics",
    ) -> None:
        if (duckdb_path is None) == (engine is None):
            raise ValueError("pass exactly one of duckdb_path or engine")
        self._duckdb_path = duckdb_path
        self._engine = engine
        self._catalog = catalog
        self._schema = schema
        self._connection: Any = None

    def __enter__(self) -> Warehouse:
        if self._duckdb_path is not None:
            import duckdb

            self._connection = duckdb.connect(str(self._duckdb_path), read_only=True)
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the DuckDB connection, if this warehouse owns one."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def relation(self, name: str, *, schema: str | None = None) -> str:
        """Return a quoted relation name for this engine."""
        schema_name = schema or self._schema
        for part in (schema_name, name):
            if not _IDENTIFIER.match(part):
                raise ValueError(f"invalid SQL identifier: {part!r}")
        if self._duckdb_path is not None:
            return f'"{schema_name}"."{name}"'
        if not _IDENTIFIER.match(self._catalog):
            raise ValueError(f"invalid catalog identifier: {self._catalog!r}")
        return f'"{self._catalog}"."{schema_name}"."{name}"'

    def query(self, sql: str) -> list[dict[str, Any]]:
        """Run one read-only query and return rows as dictionaries."""
        with self._cursor() as cursor:
            cursor.execute(sql)
            columns = [str(description[0]) for description in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        """Yield a cursor for either a DuckDB connection or a Trino resource.

        ``TrinoResource.cursor`` is a context manager; a DuckDB connection's
        ``cursor`` is not. This adapter hides the difference.
        """
        if self._connection is not None:
            cursor = self._connection.cursor()
            try:
                yield cursor
            finally:
                cursor.close()
            return
        if self._engine is None:
            raise RuntimeError("warehouse is not open; use it as a context manager")
        with self._engine.cursor() as cursor:
            yield cursor
