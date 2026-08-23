"""Mock Iceberg catalog backed by DuckDB for fast unit testing.

Implements a subset of PyIceberg's Catalog interface using an in-memory
DuckDB database, enabling tests to run without the full Iceberg/Nessie stack.

Example:
    >>> catalog = MockIcebergCatalog()
    >>> # Use with any schema dict like {"id": "int", "name": "string"}
    >>> table = catalog.create_table("raw.users", schema={"id": "int", "name": "string"})
    >>> df = pd.DataFrame({"id": [1, 2], "name": ["Alice", "Bob"]})
    >>> table.append(df)
    >>> result = table.scan().to_pandas()

"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Optional, Sequence, Union

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


def _normalize_type(dtype: str) -> str:
    """Normalize a type string to a DuckDB type.

    Handles PyIceberg types, Python types, and plain strings; unknown
    types default to VARCHAR.
    """
    dtype_str = str(dtype).lower()

    type_mapping = {
        "int32": "INTEGER",
        "int64": "BIGINT",
        "int": "INTEGER",
        "long": "BIGINT",
        "float": "FLOAT",
        "double": "DOUBLE",
        "string": "VARCHAR",
        "str": "VARCHAR",
        "bool": "BOOLEAN",
        "boolean": "BOOLEAN",
        "date": "DATE",
        "timestamp": "TIMESTAMP",
        "datetime": "TIMESTAMP",
        "object": "VARCHAR",
        "bytes": "BLOB",
    }

    for key, val in type_mapping.items():
        if key in dtype_str:
            return val

    # Default to VARCHAR for unknown types
    return "VARCHAR"


@dataclass
class MockTable:
    """Mock Iceberg table backed by DuckDB.

    Stores metadata in Python and actual data in an in-memory DuckDB
    database.
    """

    name: str
    schema: Union[dict[str, str], Any]  # Dict or PyIceberg Schema
    _db: duckdb.DuckDBPyConnection
    _catalog: Optional["MockIcebergCatalog"] = None

    def __post_init__(self) -> None:
        """Initialize table in DuckDB."""
        self._create_duckdb_table()

    def _create_duckdb_table(self) -> None:
        """Create the actual table in DuckDB."""
        namespace, table_name = self.name.split(".")
        full_name = f"{namespace}_{table_name}"

        # Build CREATE TABLE statement from schema
        columns = []

        if isinstance(self.schema, dict):
            # Simple dict schema: {"col_name": "type_string"}
            for col_name, col_type in self.schema.items():
                duckdb_type = _normalize_type(col_type)
                columns.append(f"{col_name} {duckdb_type}")
        else:
            # PyIceberg Schema object
            for field in self.schema.fields:
                duckdb_type = _normalize_type(str(field.type))
                nullable = "NULL" if field.optional else "NOT NULL"
                columns.append(f"{field.name} {duckdb_type} {nullable}")

        _validate_identifier(full_name, "table name")
        create_stmt = f"CREATE TABLE {full_name} ({', '.join(columns)})"
        try:
            self._db.execute(create_stmt)
        except duckdb.CatalogException:
            # Table already exists
            logger.debug(
                "mock_iceberg_create_table_exists",
                table_name=self.name,
                duckdb_table=full_name,
            )

    def append(self, df: pd.DataFrame) -> None:
        """Append a DataFrame to the table.

        Raises ValueError when the DataFrame columns do not match the
        table schema.
        """
        namespace, table_name = self.name.split(".")
        full_name = f"{namespace}_{table_name}"

        self._validate_schema(df)

        self._db.from_df(df).insert_into(full_name)

    def overwrite(self, df: pd.DataFrame) -> None:
        """Replace the table contents with a DataFrame."""
        namespace, table_name = self.name.split(".")
        full_name = f"{namespace}_{table_name}"

        self._validate_schema(df)

        _validate_identifier(full_name, "table name")
        self._db.execute(f"DELETE FROM {full_name}")
        self._db.from_df(df).insert_into(full_name)

    def scan(self) -> "MockTableScan":
        """Return a MockTableScan for querying the table."""
        return MockTableScan(self)

    def _validate_schema(self, df: pd.DataFrame) -> None:
        """Validate that a DataFrame's columns match the table schema."""
        df_cols = set(df.columns)

        if isinstance(self.schema, dict):
            table_cols = set(self.schema.keys())
        else:
            # PyIceberg Schema
            table_cols = {field.name for field in self.schema.fields}

        if df_cols != table_cols:
            missing = table_cols - df_cols
            extra = df_cols - table_cols
            msg = f"Schema mismatch for {self.name}"
            if missing:
                msg += f"\nMissing columns: {missing}"
            if extra:
                msg += f"\nExtra columns: {extra}"
            raise ValueError(msg)

    @property
    def full_name(self) -> str:
        """Return the full DuckDB table name with namespace prefix."""
        namespace, table_name = self.name.split(".")
        return f"{namespace}_{table_name}"


class MockTableScan:
    """Results from scanning a MockTable.

    Provides methods to execute scans and return results in various formats.

    """

    def __init__(self, table: MockTable) -> None:
        """Initialize the scan for a table."""
        self.table = table

    def to_pandas(self) -> pd.DataFrame:
        """Execute the scan and return the results as a pandas DataFrame."""
        query = f"SELECT * FROM {self.table.full_name}"
        result = self.table._db.execute(query).fetchall()

        if not result:
            # Return empty DataFrame with correct schema
            if isinstance(self.table.schema, dict):
                return pd.DataFrame({col: [] for col in self.table.schema.keys()})
            else:
                return pd.DataFrame({field.name: [] for field in self.table.schema.fields})

        # Get column names from cursor description
        col_names = [desc[0] for desc in self.table._db.description]
        return pd.DataFrame(result, columns=col_names)

    def to_arrow(self) -> Any:
        """Execute the scan and return the results as a PyArrow Table.

        Raises ImportError when PyArrow is not installed.
        """
        try:
            import pyarrow as pa

            df = self.to_pandas()
            return pa.Table.from_pandas(df)
        except ImportError:
            raise ImportError("PyArrow required for to_arrow(). Install: pip install pyarrow")


class MockIcebergCatalog:
    """In-memory Iceberg catalog mock using DuckDB backend.

    Implements a subset of PyIceberg's Catalog interface for testing.
    Tables are stored in DuckDB with metadata tracked in Python dicts.

    Example:
        >>> catalog = MockIcebergCatalog()
        >>> schema = {"id": "int", "name": "string"}
        >>> table = catalog.create_table("raw.users", schema=schema)
        >>> df = pd.DataFrame({"id": [1, 2], "name": ["Alice", "Bob"]})
        >>> table.append(df)

    """

    def __init__(self) -> None:
        """Initialize in-memory DuckDB catalog."""
        self._db = duckdb.connect(":memory:")
        self._tables: dict[str, MockTable] = {}
        self._namespaces: set[str] = set()

        # Pre-create the standard Phlo namespaces so tests can use them without
        # setup calls.
        for ns in ["raw", "bronze", "silver", "gold", "marts"]:
            self.create_namespace(ns)

    def create_namespace(self, namespace: str) -> None:
        """Create a namespace.

        Raises ValueError if the namespace already exists.
        """
        if namespace in self._namespaces:
            raise ValueError(f"Namespace {namespace} already exists")

        self._namespaces.add(namespace)

        try:
            self._db.execute(f'CREATE SCHEMA "{namespace}"')
        except duckdb.CatalogException:
            # Schema might already exist
            logger.debug("mock_iceberg_namespace_exists", namespace=namespace)

    def drop_namespace(self, namespace: str) -> None:
        """Drop a namespace."""
        self._namespaces.discard(namespace)
        try:
            self._db.execute(f'DROP SCHEMA IF EXISTS "{namespace}"')
        except duckdb.CatalogException:
            logger.debug("mock_iceberg_drop_namespace_ignored", namespace=namespace)

    def create_table(
        self,
        identifier: str,
        schema: Union[dict[str, str], Any],
        partition_spec: Optional[Sequence[tuple[str, str]]] = None,
        if_not_exists: bool = False,
    ) -> MockTable:
        """Create a new table.

        schema may be a dict like {"col": "type"} or a PyIceberg Schema.
        Partitioning via partition_spec is accepted but not fully
        supported. With if_not_exists=True an existing matching table is
        returned instead of raising; otherwise raises ValueError when the
        table already exists. Creates the table's namespace if needed.
        """
        if identifier in self._tables:
            if if_not_exists:
                return self._tables[identifier]
            raise ValueError(f"Table {identifier} already exists")

        namespace = identifier.split(".")[0]
        if namespace not in self._namespaces:
            self.create_namespace(namespace)

        table = MockTable(
            name=identifier,
            schema=schema,
            _db=self._db,
            _catalog=self,
        )

        self._tables[identifier] = table
        return table

    def load_table(self, identifier: str) -> MockTable:
        """Load an existing table.

        Raises ValueError when the table does not exist.
        """
        if identifier not in self._tables:
            raise ValueError(f"Table {identifier} not found")

        return self._tables[identifier]

    def drop_table(self, identifier: str) -> None:
        """Drop a table."""
        if identifier in self._tables:
            table = self._tables.pop(identifier)
            try:
                _validate_identifier(table.full_name, "table name")
                self._db.execute(f"DROP TABLE IF EXISTS {table.full_name}")
            except duckdb.CatalogException:
                logger.debug(
                    "mock_iceberg_drop_table_ignored",
                    table_identifier=identifier,
                    duckdb_table=table.full_name,
                )

    def list_tables(self, namespace: str | None = None) -> list[str]:
        """List tables in a namespace.

        With no namespace, returns all table identifiers.
        """
        if namespace is None:
            return sorted(self._tables)
        return [
            identifier
            for identifier in self._tables.keys()
            if identifier.startswith(f"{namespace}.")
        ]

    def list_namespaces(self) -> list[str]:
        """List all namespaces."""
        return sorted(self._namespaces)

    def rename_table(self, old_identifier: str, new_identifier: str) -> None:
        """Rename a table.

        Raises ValueError when the old table does not exist.
        """
        if old_identifier not in self._tables:
            raise ValueError(f"Table {old_identifier} not found")

        table = self._tables.pop(old_identifier)
        table.name = new_identifier
        self._tables[new_identifier] = table

        old_full = old_identifier.replace(".", "_")
        new_full = new_identifier.replace(".", "_")
        _validate_identifier(old_full, "old table name")
        _validate_identifier(new_full, "new table name")
        try:
            self._db.execute(f"ALTER TABLE {old_full} RENAME TO {new_full}")
        except duckdb.CatalogException:
            logger.warning(
                "mock_iceberg_rename_duckdb_failed",
                old_identifier=old_identifier,
                new_identifier=new_identifier,
                exc_info=True,
            )

    def table_exists(self, identifier: str) -> bool:
        """Return whether a table exists."""
        return identifier in self._tables

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Context manager for transactions (no-op in this mock)."""
        try:
            yield
        except Exception:
            logger.warning("mock_iceberg_transaction_failed", exc_info=True)
            raise

    def close(self) -> None:
        """Close the database connection."""
        if self._db:
            self._db.close()

    def __enter__(self) -> "MockIcebergCatalog":
        """Enter the context manager."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()
