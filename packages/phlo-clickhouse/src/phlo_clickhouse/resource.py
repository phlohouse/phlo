"""ClickHouse resource for executing queries.

This module provides the ClickHouseResource class for managing ClickHouse
database connections, executing queries, and handling data operations
including table creation and Parquet file ingestion.

Example:
    Basic resource usage:

    >>> from phlo_clickhouse.resource import ClickHouseResource
    >>> resource = ClickHouseResource()
    >>> resource.execute("SELECT 1")
    [[1]]


Re-exported as ClickHouseResource from the phlo_clickhouse package root; builds
on the capability contracts in phlo.capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any, Iterable

import clickhouse_connect
import pandas as pd
import pyarrow as pa

from phlo.capabilities import CapabilitySupport
from phlo.logging import get_logger
from phlo_clickhouse.settings import get_settings as get_clickhouse_settings

if TYPE_CHECKING:
    from clickhouse_connect.driver import Client
    from pandera.pandas import DataFrameModel

logger = get_logger(__name__)

CLICKHOUSE_QUERY_ENGINE_SUPPORT = CapabilitySupport(
    supports_snapshots=False,
    supports_time_travel=False,
)


@dataclass
class ClickHouseResource:
    """Resource wrapper for ClickHouse connections and query execution.

    Manages database connections, query execution, table management, and
    data ingestion. Every connection field falls back to the configured
    settings when left as None.

    Example:
        >>> resource = ClickHouseResource(host="localhost", database="test")
        >>> client = resource.get_client()

    """

    host: str | None = None
    port: int | None = None
    user: str | None = None
    password: str | None = None
    database: str | None = None
    secure: bool | None = None

    def _settings(self):
        """Return the ClickHouseSettings instance with configured defaults."""
        return get_clickhouse_settings()

    def get_client(self) -> "Client":
        """Return a connected ClickHouse client built from the resource fields
        or settings defaults.

        Example:
            >>> resource = ClickHouseResource()
            >>> client = resource.get_client()
            >>> result = client.query("SELECT 1")

        """
        settings = self._settings()
        return clickhouse_connect.get_client(
            host=self.host or settings.clickhouse_host,
            port=self.port or settings.clickhouse_http_port,
            username=self.user or settings.clickhouse_user,
            password=self.password or settings.clickhouse_password,
            database=self.database or settings.clickhouse_db,
            secure=self.secure if self.secure is not None else settings.clickhouse_secure,
        )

    def execute(self, sql: str, params: Iterable[object] | None = None) -> list[list[Any]]:
        """Run a SQL query and return its rows, each a list of column values;
        the connection is closed afterwards.

        Example:
            >>> resource = ClickHouseResource()
            >>> rows = resource.execute("SELECT number FROM system.numbers LIMIT 3")
            >>> len(rows)
            3

        """
        client = self.get_client()
        try:
            result = client.query(sql, parameters=list(params or []))
            return [list(row) for row in result.result_rows]
        finally:
            client.close()

    def command(self, sql: str) -> Any:
        """Execute a DDL/DML statement (CREATE TABLE, INSERT, ALTER, ...) that
        returns a single value or None; the connection is closed afterwards.

        Example:
            >>> resource = ClickHouseResource()
            >>> result = resource.command("CREATE TABLE test (id Int32) ENGINE = Memory")

        """
        client = self.get_client()
        try:
            return client.command(sql)
        finally:
            client.close()

    def wait_ready(
        self,
        *,
        timeout: float = 60.0,
        interval: float = 1.0,
    ) -> None:
        """Poll with a health-check query until ClickHouse responds, retrying
        every interval seconds. Raises TimeoutError if not ready within
        timeout seconds (default 60.0).

        Example:
            >>> resource = ClickHouseResource()
            >>> resource.wait_ready(timeout=30.0)  # Blocks until ready

        """
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        interval = max(interval, 0.0)
        settings = self._settings()
        while time.monotonic() < deadline:
            try:
                self.command("SELECT 1")
                logger.info(
                    "clickhouse_wait_ready_succeeded",
                    host=self.host or settings.clickhouse_host,
                    port=self.port or settings.clickhouse_http_port,
                )
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.debug(
                    "clickhouse_wait_ready_retry",
                    host=self.host or settings.clickhouse_host,
                    port=self.port or settings.clickhouse_http_port,
                    retry_interval_seconds=interval,
                )
                time.sleep(interval)
        logger.error(
            "clickhouse_wait_ready_timeout",
            host=self.host or settings.clickhouse_host,
            port=self.port or settings.clickhouse_http_port,
            timeout_seconds=timeout,
        )
        raise TimeoutError(f"ClickHouse not ready after {timeout:.1f}s") from last_error

    def _escape_identifier(self, name: str) -> str:
        """Wrap an identifier in backticks, doubling any embedded backticks,
        for safe use in SQL statements.

        Example:
            >>> resource = ClickHouseResource()
            >>> resource._escape_identifier("my-table")
            '`my-table`'

        """
        return f"`{name.replace('`', '``')}`"

    def _resolve_target(self, table_name: str) -> tuple[str, str]:
        """Split a possibly namespace-qualified table name into escaped
        ``(database, table)`` identifiers.

        Ingestion assets address tables as ``<namespace>.<table>`` (for
        example ``raw.platform_events``); the namespace selects the ClickHouse
        database so tables land where dbt sources expect them. A bare name
        falls back to the configured default database.

        Example:
            >>> resource._resolve_target("raw.platform_events")
            ('`raw`', '`platform_events`')

        """
        settings = self._settings()
        if "." in table_name:
            namespace, _, table = table_name.partition(".")
            database = namespace or settings.clickhouse_db
        else:
            database = settings.clickhouse_db
            table = table_name
        return self._escape_identifier(database), self._escape_identifier(table)

    def ensure_table(
        self,
        *,
        table_name: str,
        schema: Any,
        partition_spec: Any = None,
        override_ref: str | None = None,
    ) -> Any:
        """Create the destination table if it does not exist, with optional
        partitioning from (column_name, type) tuples.

        The table name may carry a ``<database>.<table>`` namespace; the
        database is created on demand so ingestion can land in namespaces
        other than the configured default.

        Example:
            >>> from pandera import Schema, Column, Int64
            >>> class MySchema(Schema):
            ...     id = Column(Int64)
            >>> resource = ClickHouseResource()
            >>> resource.ensure_table(table_name="raw.my_table", schema=MySchema)

        """
        database, table = self._resolve_target(table_name)
        if "." in table_name:
            # Ingestion namespaces may target databases that do not exist yet.
            namespace = table_name.partition(".")[0]
            self.command(f"CREATE DATABASE IF NOT EXISTS {self._escape_identifier(namespace)}")

        columns_def = self._schema_to_columns(schema)

        partition_by = ""
        if partition_spec:
            partition_cols = [self._escape_identifier(p[0]) for p in partition_spec]
            partition_by = f"PARTITION BY ({', '.join(partition_cols)})"

        sql = f"CREATE TABLE IF NOT EXISTS {database}.{table} ({columns_def}) ENGINE = MergeTree() {partition_by} ORDER BY tuple()"

        return self.command(sql)

    def append_parquet(
        self,
        *,
        table_name: str,
        data_path: str | Path,
        override_ref: str | None = None,
    ) -> dict[str, int]:
        """Read a Parquet file and insert all of its rows into the table.
        Returns the inserted row count.

        Example:
            >>> resource = ClickHouseResource()
            >>> result = resource.append_parquet(
            ...     table_name="events",
            ...     data_path="/data/events.parquet"
            ... )
            >>> result["rows_inserted"]
            1000

        """
        database, table = self._resolve_target(table_name)

        data_path_str = str(data_path)
        df = pd.read_parquet(data_path_str)
        row_count = len(df)

        client = self.get_client()
        try:
            client.insert_df(f"{database}.{table}", df)
        finally:
            client.close()

        return {"rows_inserted": row_count}

    def merge_parquet(
        self,
        *,
        table_name: str,
        data_path: str | Path,
        unique_key: str,
        override_ref: str | None = None,
    ) -> dict[str, int]:
        """Upsert Parquet data: delete existing rows whose unique_key matches
        the incoming data, then insert it. Returns inserted and deleted row
        counts. The delete is a background mutation, so readers may
        transiently see both versions of a key.

        Example:
            >>> resource = ClickHouseResource()
            >>> result = resource.merge_parquet(
            ...     table_name="events",
            ...     data_path="/data/events.parquet",
            ...     unique_key="event_id"
            ... )
            >>> result["rows_inserted"], result["rows_deleted"]
            (100, 100)

        """
        database, table = self._resolve_target(table_name)
        key = self._escape_identifier(unique_key)

        data_path_str = str(data_path)
        df = pd.read_parquet(data_path_str)
        row_count = len(df)

        unique_keys = df[unique_key].tolist()
        # ALTER TABLE ... DELETE is a background mutation and returns before
        # affected rows are actually removed (mutations_sync is left unset).
        # The insert below therefore races the delete: readers may transiently
        # observe both the old and the new version of a key.
        if unique_keys:
            keys_str = ", ".join(f"'{k}'" for k in unique_keys)
            delete_sql = f"ALTER TABLE {database}.{table} DELETE WHERE {key} IN ({keys_str})"
            self.command(delete_sql)

        client = self.get_client()
        try:
            client.insert_df(f"{database}.{table}", df)
        finally:
            client.close()

        return {"rows_inserted": row_count, "rows_deleted": len(unique_keys)}

    def _schema_to_columns(self, schema: Any) -> str:
        """Render a schema as comma-separated "name type" column definitions.
        Accepts Pandera-style schemas (``columns`` attribute), dataclass-like
        schemas (``fields`` attribute), and pyarrow schemas - the form
        produced by ``schema_from_validation_schema``. Raises TypeError for
        unsupported schema types.

        Example:
            >>> import pyarrow as pa
            >>> resource = ClickHouseResource()
            >>> resource._schema_to_columns(pa.schema([("id", pa.int64())]))
            '`id` Int64'

        """
        if isinstance(schema, pa.Schema):
            columns = [
                f"{self._escape_identifier(field.name)} "
                f"{self._arrow_type_to_clickhouse(field.type)}"
                for field in schema
            ]
            return ", ".join(columns)

        if hasattr(schema, "to_schema"):
            schema = schema.to_schema()

        if hasattr(schema, "columns"):
            columns = []
            for name, col in schema.columns.items():
                ch_type = self._pandas_type_to_clickhouse(col.dtype)
                columns.append(f"{name} {ch_type}")
            return ", ".join(columns)

        if hasattr(schema, "fields"):
            columns = []
            for field in schema.fields:
                ch_type = self._python_type_to_clickhouse(field.type)
                columns.append(f"{field.name} {ch_type}")
            return ", ".join(columns)

        raise TypeError(
            f"Unsupported schema type: {type(schema).__name__}. Expected a schema with 'columns' or 'fields' attribute."
        )

    def _arrow_type_to_clickhouse(self, arrow_type: Any) -> str:
        """Map an arrow type to its ClickHouse type name, defaulting to
        "String" for unrecognized types.

        Example:
            >>> import pyarrow as pa
            >>> resource = ClickHouseResource()
            >>> resource._arrow_type_to_clickhouse(pa.int64())
            'Int64'

        """
        if pa.types.is_timestamp(arrow_type):
            if arrow_type.tz is not None:
                return "DateTime64(6, 'UTC')"
            return "DateTime64(6)"
        if pa.types.is_date(arrow_type):
            return "Date"
        type_map = {
            pa.int8(): "Int8",
            pa.int16(): "Int16",
            pa.int32(): "Int32",
            pa.int64(): "Int64",
            pa.uint8(): "UInt8",
            pa.uint16(): "UInt16",
            pa.uint32(): "UInt32",
            pa.uint64(): "UInt64",
            pa.float32(): "Float32",
            pa.float64(): "Float64",
            pa.bool_(): "Bool",
            pa.string(): "String",
            pa.large_string(): "String",
            pa.binary(): "String",
        }
        return type_map.get(arrow_type, "String")

    def schema_from_validation_schema(
        self, validation_schema: type[DataFrameModel] | type[Any]
    ) -> pa.Schema:
        """Convert a Pandera validation model to an arrow schema.

        The ClickHouse table store consumes pyarrow schemas for both DDL
        rendering and parquet coercion; reserved DLT and Phlo metadata columns
        are appended so lineage columns land with the data. Raises
        SchemaConversionError when the model cannot be converted.

        Example:
            Convert a Pandera model to an arrow schema::

                resource = ClickHouseResource()
                schema = resource.schema_from_validation_schema(UserSchema)

        """
        from phlo_clickhouse.schema_conversion import pandera_to_arrow

        return pandera_to_arrow(validation_schema)

    def _pandas_type_to_clickhouse(self, dtype: Any) -> str:
        """Map a pandas dtype to its ClickHouse type name, falling back to
        "String" for unrecognized dtypes.

        Example:
            >>> import pandas as pd
            >>> resource = ClickHouseResource()
            >>> resource._pandas_type_to_clickhouse(pd.Int64Dtype())
            'Int64'
            >>> resource._pandas_type_to_clickhouse(pd.StringDtype())
            'String'

        """
        import pandas as pd

        if pd.api.types.is_integer_dtype(dtype):
            return "Int64"
        if pd.api.types.is_float_dtype(dtype):
            return "Float64"
        if pd.api.types.is_bool_dtype(dtype):
            return "UInt8"
        if pd.api.types.is_datetime64_any_dtype(dtype):
            return "DateTime64"
        if pd.api.types.is_string_dtype(dtype):
            return "String"
        return "String"

    def _python_type_to_clickhouse(self, py_type: Any) -> str:
        """Map a Python built-in type to its ClickHouse type name, defaulting
        to "String" for unknown types.

        Example:
            >>> resource = ClickHouseResource()
            >>> resource._python_type_to_clickhouse(int)
            'Int64'
            >>> resource._python_type_to_clickhouse(str)
            'String'

        """
        type_map = {
            int: "Int64",
            float: "Float64",
            str: "String",
            bool: "UInt8",
        }
        return type_map.get(py_type, "String")
