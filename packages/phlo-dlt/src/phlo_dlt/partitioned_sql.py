"""Helpers for partitioned SQL sources used by DLT ingestion workflows.

Loads a caller-configured SQL template, binds per-partition window bounds
and parameters, executes it on a short-lived connection, and yields rows with
normalized snake_case keys wrapped as a DLT resource or source. Connections
and cursors are closed quietly, even when row iteration fails.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any


ConnectFactory = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class PartitionWindow:
    """Partition bounds passed into a source SQL template."""

    partition_key: str
    start: Any
    end: Any


@dataclass(frozen=True, slots=True)
class PartitionedSqlConfig:
    """Configuration for extracting normalized rows from one SQL partition."""

    sql_template_path: str | Path | None = None
    sql_template_package: str | None = None
    sql_template_name: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)
    row_defaults: Mapping[str, Any] = field(default_factory=dict)
    fetch_size: int = 1000

    def __post_init__(self) -> None:
        if self.fetch_size < 1:
            raise ValueError("fetch_size must be at least 1")
        if self.sql_template_path is None and not (
            self.sql_template_package and self.sql_template_name
        ):
            raise ValueError(
                "Provide sql_template_path or both sql_template_package and sql_template_name"
            )


def load_sql_template(config: PartitionedSqlConfig) -> str:
    """Load the configured SQL template from a path or installed package resources."""
    if config.sql_template_path is not None:
        path = Path(config.sql_template_path)
        if not path.exists():
            raise FileNotFoundError(f"SQL template not found: {path}")
        return path.read_text(encoding="utf-8")

    assert config.sql_template_package is not None
    assert config.sql_template_name is not None
    try:
        return (
            resources.files(config.sql_template_package)
            .joinpath(config.sql_template_name)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        template = f"{config.sql_template_package}:{config.sql_template_name}"
        raise FileNotFoundError(f"SQL template not found: {template}") from exc


def normalize_column_name(name: str) -> str:
    """Normalize source column labels to stable snake_case row keys."""
    normalized = re.sub(r"[^0-9A-Za-z]+", "_", str(name)).strip("_").lower()
    if not normalized:
        normalized = "column"
    if normalized[0].isdigit():
        normalized = f"_{normalized}"
    return normalized


def _normalized_columns(description: Any) -> list[str]:
    names: list[str] = []
    seen: dict[str, int] = {}
    for column in description or []:
        raw_name = (
            column[0] if isinstance(column, (tuple, list)) else getattr(column, "name", column)
        )
        normalized = normalize_column_name(str(raw_name))
        count = seen.get(normalized, 0)
        seen[normalized] = count + 1
        names.append(normalized if count == 0 else f"{normalized}_{count + 1}")
    return names


def _close_quietly(resource: Any) -> None:
    close = getattr(resource, "close", None)
    if callable(close):
        close()


def _bind_params(config: PartitionedSqlConfig, window: PartitionWindow) -> dict[str, Any]:
    params = dict(config.params)
    params.update(
        {
            "partition_key": window.partition_key,
            "partition_start": window.start,
            "partition_end": window.end,
        }
    )
    return params


def run_partitioned_sql(
    config: PartitionedSqlConfig,
    *,
    window: PartitionWindow,
    connect: ConnectFactory,
) -> Iterator[dict[str, Any]]:
    """Execute configured SQL for one partition and yield normalized row dictionaries."""
    sql = load_sql_template(config)
    connection = connect()
    cursor = connection.cursor()
    try:
        cursor.execute(sql, _bind_params(config, window))
        columns = _normalized_columns(getattr(cursor, "description", None))
        base_defaults = {"partition_date": window.partition_key}
        base_defaults.update(config.row_defaults)

        while True:
            rows = cursor.fetchmany(config.fetch_size)
            if not rows:
                break
            for row in rows:
                values = dict(zip(columns, row, strict=False))
                normalized_row = dict(base_defaults)
                normalized_row.update(values)
                yield normalized_row
    finally:
        _close_quietly(cursor)
        _close_quietly(connection)


def partitioned_sql_resource(
    config: PartitionedSqlConfig,
    *,
    window: PartitionWindow,
    connect: ConnectFactory,
    name: str | None = None,
    primary_key: str | list[str] | None = None,
    merge_key: str | list[str] | None = None,
    write_disposition: str | None = None,
) -> Any:
    """Wrap a partitioned SQL generator as a DLT resource."""
    import dlt

    resource_kwargs: dict[str, Any] = {}
    if name is not None:
        resource_kwargs["name"] = name
    if primary_key is not None:
        resource_kwargs["primary_key"] = primary_key
    if merge_key is not None:
        resource_kwargs["merge_key"] = merge_key
    if write_disposition is not None:
        resource_kwargs["write_disposition"] = write_disposition

    @dlt.resource(**resource_kwargs)
    def _resource() -> Iterator[dict[str, Any]]:
        yield from run_partitioned_sql(config, window=window, connect=connect)

    return _resource()


def partitioned_sql_source(
    config: PartitionedSqlConfig,
    *,
    window: PartitionWindow,
    connect: ConnectFactory,
    source_name: str,
    resource_name: str | None = None,
    primary_key: str | list[str] | None = None,
    merge_key: str | list[str] | None = None,
    write_disposition: str | None = None,
) -> Any:
    """Wrap a partitioned SQL resource as a DLT source."""
    import dlt

    @dlt.source(name=source_name)
    def _source() -> list[Any]:
        return [
            partitioned_sql_resource(
                config,
                window=window,
                connect=connect,
                name=resource_name,
                primary_key=primary_key,
                merge_key=merge_key,
                write_disposition=write_disposition,
            )
        ]

    return _source()


__all__ = [
    "PartitionWindow",
    "PartitionedSqlConfig",
    "load_sql_template",
    "normalize_column_name",
    "partitioned_sql_resource",
    "partitioned_sql_source",
    "run_partitioned_sql",
]
