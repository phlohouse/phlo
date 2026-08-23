"""Mock Trino resource backed by DuckDB for testing.

Provides a mock implementation of TrinoResource that uses DuckDB as the backend,
enabling SQL testing without requiring a real Trino server.

Example:
    >>> trino = MockTrinoResource()
    >>> cursor = trino.cursor()
    >>> cursor.execute("CREATE TABLE test AS SELECT 1 as id")
    >>> result = cursor.execute("SELECT * FROM test")
    >>> print(cursor.fetchall())

"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Iterator, Literal, Optional

import duckdb
import pandas as pd
from phlo.logging import get_logger

logger = get_logger(__name__)

_SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_identifier(name: str, context: str = "identifier") -> str:
    """Validate a SQL identifier to prevent injection in mock code."""
    if not _SAFE_IDENTIFIER_RE.match(name):
        raise ValueError(f"Unsafe SQL {context}: {name!r}")
    return name


class MockCursor:
    """Mock Trino cursor backed by DuckDB, implementing the DB-API 2.0 cursor interface."""

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        """Initialize cursor around an existing DuckDB connection."""
        self._connection = connection
        self._result = None
        self._description = None
        self._row_index = 0

    def execute(self, query: str, params: Optional[tuple] = None) -> "MockCursor":
        """Translate, run, and return self for chaining; wraps any failure in RuntimeError."""
        try:
            query = self._translate_query(query)

            result = self._connection.execute(query)
            self._result = result

            try:
                cols = getattr(result, "columns", None)
                if cols:
                    self._description = [
                        (name, "VARCHAR")  # Simplified type mapping
                        for name in cols
                    ]
                else:
                    self._description = None
            except (AttributeError, TypeError):
                # Fallback if no columns attribute
                self._description = None

            self._row_index = 0
            return self

        except Exception as e:
            logger.warning("mock_trino_query_execution_failed", query=query, exc_info=True)
            raise RuntimeError(f"Query execution failed: {e}") from e

    def fetchall(self) -> list[tuple]:
        """Return every result row as tuples; empty list when no query has run."""
        if self._result is None:
            return []

        rows = self._result.fetchall()
        self._row_index = len(rows)
        return rows

    def fetchone(self) -> Optional[tuple]:
        """Return the next row as a tuple, or None when exhausted."""
        if self._result is None:
            return None

        row = self._result.fetchone()
        if row is not None:
            self._row_index += 1
        return row

    def fetchmany(self, size: int = 1) -> list[tuple]:
        """Advance through and return up to ``size`` result rows as tuples."""
        if self._result is None:
            return []

        rows = self._result.fetchmany(size)
        self._row_index += len(rows)
        return rows

    def fetchdf(self) -> pd.DataFrame:
        """Return all results as a DataFrame; empty when no query has run."""
        if self._result is None:
            return pd.DataFrame()

        return self._result.df()

    @property
    def description(self) -> Optional[list]:
        """Return column descriptors from the last query."""
        return self._description

    @property
    def rowcount(self) -> int:
        """Return the consumed-row count, or -1 when no query has run."""
        return self._row_index if self._result else -1

    def close(self) -> None:
        """Close cursor."""
        self._result = None
        self._description = None

    @staticmethod
    def _translate_query(query: str) -> str:
        """Pass Trino SQL through unchanged; add DuckDB rewrites here when a test needs them."""
        # Trino and DuckDB SQL overlap enough that queries currently pass
        # through unchanged. Add rewrites here when a test needs a Trino-only
        # construct that DuckDB rejects.
        return query


class MockConnection:
    """Mock Trino connection backed by DuckDB, implementing the DB-API 2.0 connection interface."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8080,
        user: str = "user",
        catalog: str = "memory",
        schema: Optional[str] = None,
    ) -> None:
        """Open an in-memory DuckDB connection; all parameters exist for compatibility."""
        self._db = duckdb.connect(":memory:")
        self.catalog = catalog
        self.schema = schema
        self._tables: dict[str, pd.DataFrame] = {}

    def cursor(self) -> MockCursor:
        """Return a MockCursor over the shared DuckDB connection."""
        return MockCursor(self._db)

    def execute(self, query: str) -> MockCursor:
        """Run the query on a fresh cursor and return that cursor."""
        cursor = self.cursor()
        cursor.execute(query)
        return cursor

    def commit(self) -> None:
        """Commit transaction (no-op in mock)."""
        pass

    def rollback(self) -> None:
        """Rollback transaction (no-op in mock)."""
        pass

    def close(self) -> None:
        """Close connection."""
        if self._db:
            self._db.close()

    def __enter__(self) -> "MockConnection":
        """Return self so the connection can be used as a context manager."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()


class MockTrinoResource:
    """Drop-in TrinoResource replacement backed by DuckDB for SQL tests without a server.

    Connection attributes (host, port, user, catalog, trino_schema) exist for
    compatibility and are ignored.

    Example:
        >>> trino = MockTrinoResource()
        >>> cursor = trino.cursor()
        >>> cursor.execute("SELECT 1 as id")
        >>> results = cursor.fetchall()

    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8080,
        user: str = "dagster",
        catalog: str = "memory",
        trino_schema: Optional[str] = None,
    ) -> None:
        """Initialize the mock resource; connection parameters exist for compatibility."""
        self.host = host
        self.port = port
        self.user = user
        self.catalog = catalog
        self.trino_schema = trino_schema
        self._db = duckdb.connect(":memory:")
        self._tables: dict[str, pd.DataFrame] = {}

    def get_connection(
        self,
        schema: Optional[str] = None,
        branch: Optional[Literal["main", "dev"]] = None,
    ) -> MockConnection:
        """Return a MockConnection using the given or default schema; branch is ignored."""
        return MockConnection(
            host=self.host,
            port=self.port,
            user=self.user,
            catalog=self.catalog,
            schema=schema or self.trino_schema,
        )

    @contextmanager
    def connection(
        self,
        schema: Optional[str] = None,
        branch: Optional[Literal["main", "dev"]] = None,
    ) -> Iterator[MockConnection]:
        """Yield a MockConnection and close it afterwards; branch is ignored."""
        conn = self.get_connection(schema=schema, branch=branch)
        try:
            yield conn
        finally:
            conn.close()

    def cursor(
        self,
        schema: Optional[str] = None,
        branch: Optional[Literal["main", "dev"]] = None,
    ) -> MockCursor:
        """Return a MockCursor over the resource's DuckDB connection; branch is ignored."""
        return MockCursor(self._db)

    def execute(
        self,
        query: str,
        schema: Optional[str] = None,
        branch: Optional[Literal["main", "dev"]] = None,
    ) -> list[tuple]:
        """Run the query and return all result rows as tuples; branch is ignored."""
        cursor = self.cursor(schema=schema, branch=branch)
        cursor.execute(query)
        return cursor.fetchall()

    def query_with_schema(
        self,
        query: str,
        schema_class: type[Any],
        schema: Optional[str] = None,
        branch: Optional[Literal["main", "dev"]] = None,
    ) -> pd.DataFrame:
        """Execute the query and coerce result types from a Pandera schema class.

        Saves manual type conversion in quality checks; branch is ignored.

        Example:
            >>> from phlo_testing import MockTrinoResource
            >>> from workflows.schemas.orders import FactOrders
            >>> trino = MockTrinoResource()
            >>> df = trino.query_with_schema(
            ...     "SELECT * FROM gold.fct_orders",
            ...     FactOrders,
            ... )
            >>> # Types are now correct for validation

        """
        from phlo_trino.type_mapping import apply_schema_types

        cursor = self.cursor(schema=schema, branch=branch)
        cursor.execute(query)
        df = cursor.fetchdf()

        return apply_schema_types(df, schema_class)

    def load_table(self, table_name: str, df: pd.DataFrame) -> None:
        """Register a DataFrame as a queryable test table.

        Example:
            >>> trino = MockTrinoResource()
            >>> df = pd.DataFrame({"id": [1, 2], "name": ["Alice", "Bob"]})
            >>> trino.load_table("test.users", df)
            >>> cursor = trino.cursor()
            >>> cursor.execute("SELECT * FROM test.users")

        """
        self._tables[table_name] = df

        _validate_identifier(table_name.replace(".", "_"), "table name")
        self._db.register(table_name.replace(".", "_"), df)

    def get_table(self, table_name: str) -> Optional[pd.DataFrame]:
        """Return the loaded test table, or None when it was never registered."""
        return self._tables.get(table_name)

    def list_tables(self, schema: Optional[str] = None) -> list[str]:
        """Return the names of all loaded test tables; schema is ignored."""
        return list(self._tables.keys())

    def close(self) -> None:
        """Close all connections."""
        if self._db:
            self._db.close()

    def __enter__(self) -> "MockTrinoResource":
        """Return self so the resource can be used as a context manager."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()
