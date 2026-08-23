"""Read and write convenience helpers for workflow authors.

Every query path resolves the active query engine capability and funnels SQL
through read-only validation plus optional LIMIT enforcement before
execution, so helper-generated reads can never mutate data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from phlo.capabilities import resolve_capability
from phlo.exceptions import PhloConfigError
from phlo.helpers.partitions import PartitionScope
from phlo.helpers.sql import (
    apply_where,
    limit_sql,
    render_partition_predicate,
    validate_read_only_sql,
)
from phlo.references import LogicalRelation, quote_identifier


def resolve_query_engine(name: str | None = None, *, runtime: Any = None) -> Any:
    """Resolve the active query engine provider or raise a guided error."""
    resolution = resolve_capability("query_engine", name, runtime=runtime)
    if resolution is None:
        raise PhloConfigError(
            message="No query_engine capability could be resolved",
            suggestions=["Install/configure a query engine such as phlo-trino or phlo-clickhouse."],
        )
    return resolution.provider


def safe_query(
    sql: str,
    *,
    query_engine: Any = None,
    runtime: Any = None,
    limit: int | None = None,
    schema: str | None = None,
) -> Any:
    """Execute a read-only query through a query engine."""
    engine = query_engine or resolve_query_engine(runtime=runtime)
    final_sql = limit_sql(validate_read_only_sql(sql), limit=limit)
    return engine.execute(final_sql, schema=schema)


def read_dataframe(
    query: str | LogicalRelation,
    *,
    params: list[object] | tuple[object, ...] | None = None,
    query_engine: Any = None,
    runtime: Any = None,
    schema: str | None = None,
    schema_class: type[Any] | None = None,
) -> Any:
    """Read a query or logical relation as a DataFrame through the active query engine."""
    engine = query_engine or resolve_query_engine(runtime=runtime)
    if not hasattr(engine, "read_dataframe"):
        raise PhloConfigError(
            message=f"Query engine {type(engine).__name__} does not support DataFrame reads",
            suggestions=[
                "Use a query engine with read_dataframe support, such as phlo-trino.",
                "Use phlo.helpers.safe_query for row-oriented query results.",
            ],
        )
    return engine.read_dataframe(
        query,
        params=params,
        schema=schema,
        schema_class=schema_class,
    )


def query_scalar(sql: str, *, query_engine: Any = None, runtime: Any = None) -> Any:
    """Execute a query expected to return one scalar value."""
    result = safe_query(sql, query_engine=query_engine, runtime=runtime, limit=1)
    if hasattr(result, "iloc"):
        return result.iloc[0, 0]
    if isinstance(result, list) and result:
        first = result[0]
        if isinstance(first, dict):
            return next(iter(first.values()))
        if isinstance(first, tuple | list):
            return first[0]
        return first
    return None


def query_exists(sql: str, *, query_engine: Any = None, runtime: Any = None) -> bool:
    """Return whether a read-only query returns any truthy row/value."""
    return bool(query_scalar(sql, query_engine=query_engine, runtime=runtime))


def read_table(
    table_name: str | LogicalRelation,
    *,
    columns: list[str] | None = None,
    scope: PartitionScope | None = None,
    limit: int | None = 1000,
    query_engine: Any = None,
    runtime: Any = None,
) -> Any:
    """Read a table through the active query engine."""
    engine = query_engine
    if scope is None:
        engine = query_engine or resolve_query_engine(runtime=runtime)
    if scope is None and hasattr(engine, "read_table"):
        return engine.read_table(
            table_name,
            columns=columns,
            limit=limit,
        )
    # A partition scope forces the SQL path: the native read_table fast path
    # cannot apply the rendered partition predicate.
    selected = ", ".join(quote_identifier(column) for column in columns) if columns else "*"
    rendered_table = table_name.render() if isinstance(table_name, LogicalRelation) else table_name
    sql = f"SELECT {selected} FROM {rendered_table}"
    if scope is not None:
        sql = apply_where(sql, render_partition_predicate(scope))
    return safe_query(sql, query_engine=engine, runtime=runtime, limit=limit)


def read_partition(
    table_name: str,
    partition_key: str,
    *,
    partition_column: str = "_phlo_partition_date",
    limit: int | None = 1000,
    query_engine: Any = None,
    runtime: Any = None,
) -> Any:
    """Read one table partition."""
    return read_table(
        table_name,
        scope=PartitionScope(partition_key=partition_key, partition_column=partition_column),
        limit=limit,
        query_engine=query_engine,
        runtime=runtime,
    )


def write_parquet_batch(data: Any, path: str | Path, *, index: bool = False) -> Path:
    """Write a pandas-like DataFrame or Arrow table to a parquet file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(data, "to_parquet"):
        data.to_parquet(target, index=index)
    elif hasattr(data, "write_parquet"):
        data.write_parquet(target)
    else:
        raise PhloConfigError(
            message="write_parquet_batch expects pandas-like or Arrow-like data",
            suggestions=["Pass a pandas DataFrame, Polars DataFrame, or write parquet directly."],
        )
    return target


def sample_table(table_name: str, *, limit: int = 20, query_engine: Any = None) -> Any:
    """Read a small sample from a table."""
    return read_table(table_name, limit=limit, query_engine=query_engine)
