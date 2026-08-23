"""Phlo DLT - DLT-based data ingestion package for Phlo.

This package provides DLT (Data Load Tool) based ingestion capabilities for Phlo,
enabling decorator-driven data extraction and loading into the lakehouse. It
uses quality-provider capabilities for schema validation and supports multiple
table store backends through the Phlo capability system.

Key Features:
    - Decorator-based ingestion definition (@phlo_ingestion)
    - Optional schema validation through installed quality providers
    - Support for append and merge strategies
    - Partitioned ingestion with daily scheduling
    - Write-Audit-Publish (WAP) pattern support
    - Metadata column injection for lineage tracking

Main Exports:
    - :func:`phlo_ingestion`: Primary decorator for defining ingestion pipelines
    - :func:`get_ingestion_assets`: Retrieve all registered ingestion assets

Internal Modules:
    - :mod:`phlo_dlt.decorator`: Core ingestion decorator implementation
    - :mod:`phlo_dlt.executor`: DLT ingestion execution engine
    - :mod:`phlo_dlt.dlt_helpers`: Shared utilities for DLT operations
    - :mod:`phlo_dlt.pandera_checks`: Optional Pandera-backed validation integration
    - :mod:`phlo_dlt.registry`: Table configuration and registration
    - :mod:`phlo_dlt.settings`: Package configuration settings
    - :mod:`phlo_dlt.plugin`: Plugin interface for Phlo integration
    - :mod:`phlo_dlt.scaffold`: Workflow scaffolding utilities
    - :mod:`phlo_dlt.cli_plugin`: CLI command plugin
    - :mod:`phlo_dlt.cli_workflow`: Workflow management CLI commands

Dependencies:
    - dlt: Data Load Tool for extraction
    - pyarrow: Parquet file handling
    - pandas: Data manipulation

Example:
    ```python
    from phlo_dlt import phlo_ingestion
    from my_schemas import UserSchema

    @phlo_ingestion(
        table_name="users",
        unique_key="id",
        group="raw",
        validation_schema=UserSchema,
        cron="0 */6 * * *",
    )
    def load_users(partition_date: str):
        # Return DLT source or data
        return fetch_user_data(partition_date)

    # Get all registered assets
    assets = get_ingestion_assets()
    ```

See Also:
    - :mod:`phlo.ingestion`: Public API for ingestion operations
    - :mod:`phlo_dlt.decorator`: Detailed decorator documentation
    - Documentation: https://docs.phlo.dev/packages/phlo-dlt

Note:
    This package is typically accessed through ``phlo.ingestion`` rather than
    directly. Use ``import phlo`` or ``from phlo.ingestion import phlo_ingestion``.

"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def phlo_ingestion(*args: Any, **kwargs: Any) -> Callable[..., Any]:
    """Lazily resolve and forward to the ingestion decorator factory.

    Lazy loading avoids eager imports and circular dependencies during
    plugin discovery; all arguments are forwarded to
    :func:`phlo_dlt.decorator.phlo_ingestion`.

    Example:
        ```python
        from phlo_dlt import phlo_ingestion

        @phlo_ingestion(table_name="events", unique_key="id", group="raw")
        def load_events(partition_date: str):
            return fetch_events(partition_date)
        ```

    """
    from phlo_dlt.decorator import phlo_ingestion as _phlo_ingestion

    return _phlo_ingestion(*args, **kwargs)


def get_ingestion_assets() -> list[Any]:
    """Lazily resolve and return registered ingestion assets.

    Collects assets registered via the ``@phlo_ingestion`` decorator in a
    global registry during module import.

    Example:
        ```python
        from phlo_dlt import get_ingestion_assets

        assets = get_ingestion_assets()
        for asset in assets:
            print(f"Asset: {asset.key}")
        ```

    """
    from phlo_dlt.decorator import get_ingestion_assets as _get_ingestion_assets

    return _get_ingestion_assets()


def __getattr__(name: str) -> Any:
    """Lazily expose partitioned SQL helpers without importing DLT eagerly."""
    if name in {
        "PartitionWindow",
        "PartitionedSqlConfig",
        "load_sql_template",
        "normalize_column_name",
        "partitioned_sql_resource",
        "partitioned_sql_source",
        "run_partitioned_sql",
    }:
        from phlo_dlt import partitioned_sql

        return getattr(partitioned_sql, name)
    raise AttributeError(name)


__all__ = [
    "PartitionWindow",
    "PartitionedSqlConfig",
    "get_ingestion_assets",
    "load_sql_template",
    "normalize_column_name",
    "partitioned_sql_resource",
    "partitioned_sql_source",
    "phlo_ingestion",
    "run_partitioned_sql",
]
