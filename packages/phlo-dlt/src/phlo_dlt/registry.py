"""Table configuration and registry for DLT ingestion.

This module defines the TableConfig dataclass used to store table-level
configuration for DLT ingestion assets. It provides the data structure
that links table metadata, schemas, and partition specifications.

Key Components:
    - :class:`TableConfig`: Dataclass holding table configuration

Configuration Attributes:
    - table_name: Base table name (without namespace)
    - table_schema: Optional explicit table-store schema
    - validation_schema: Optional Pandera DataFrameModel for validation
    - unique_key: Column name used for deduplication/merge operations
    - group_name: Dagster asset group name
    - partition_spec: Optional partition transform specification

Namespace Resolution:
    The full_table_name property automatically prepends the configured
    default namespace (from settings) to create fully-qualified table names.

See Also:
    - :mod:`phlo_dlt.settings`: Default namespace configuration
    - :mod:`phlo_dlt.decorator`: Uses TableConfig for asset registration
    - :mod:`phlo_dlt.executor`: Uses TableConfig for table operations

Example:
    ```python
    from phlo_dlt.registry import TableConfig
    from my_schemas import UserSchema

    config = TableConfig(
        table_name="users",
        table_schema=None,  # Will derive from validation_schema
        validation_schema=UserSchema,
        unique_key="id",
        group_name="raw",
        partition_spec=None,
    )
    print(config.full_table_name)  # "raw.users"
    ```

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from phlo_dlt.settings import get_settings


@dataclass(frozen=True)
class TableConfig:
    """Configuration describing a registered ingestion table.

    Immutable dataclass that stores all configuration needed for a DLT
    ingestion table, including name, schemas, keys, and partitioning.

    A None `table_schema` is derived from `validation_schema`; the optional
    `partition_spec` holds (column, transform) tuples such as
    [("created_at", "day")]. The `full_table_name` property prefixes the
    configured default namespace.

    Example:
        ```python
        from phlo_dlt.registry import TableConfig
        from workflows.schemas.raw import UserSchema

        config = TableConfig(
            table_name="users",
            table_schema=None,
            validation_schema=UserSchema,
            unique_key="user_id",
            group_name="raw",
            partition_spec=[("created_at", "day")],
        )
        ```

    """

    table_name: str
    table_schema: Any | None
    validation_schema: type[Any] | None
    unique_key: str
    group_name: str
    partition_spec: list[tuple[str, str]] | None = None

    @property
    def full_table_name(self) -> str:
        """Return fully qualified table name with default namespace.

        Combines the configured default namespace with the table name
        to create a fully-qualified identifier for the table store.

        Example:
            ```python
            config = TableConfig(
                table_name="events",
                table_schema=None,
                validation_schema=None,
                unique_key="id",
                group_name="raw",
            )
            print(config.full_table_name)  # "raw.events"
            ```

        """
        return f"{get_settings().dlt_default_namespace}.{self.table_name}"
