"""Publish Trino marts into Postgres with hook events.

This module provides functionality for publishing analytical outputs from
Trino into Postgres with full lifecycle event emission, telemetry, and
lineage tracking.

Functions:
    publish_marts_to_target: Copy outputs to a structured publish target.
    publish_marts_to_postgres: Copy tables to Postgres with event emission.

Classes:
    TrinoPublishingSettings: Configuration for publish target defaults.
    TablePublishStats: Summary statistics for published tables.

Internal Functions:
    _resolve_publish_target: Resolve target wrapper to primitives.
    _publish_marts: Shared publish implementation.
    _ensure_schema: Create target schema if missing.
    _split_trino_qualified_name: Parse qualified table names.
    _quote_trino_identifier: Quote identifiers preserving case.
    _quote_trino_qualified_name: Quote fully qualified names.
    _copy_table: Copy single table with batch processing.
    _trino_table_ref_candidates: Build introspection query candidates.
    _describe_trino_table: Get column metadata with retry logic.
    _is_retryable_introspection_error: Check if error allows retry.
    _trino_type_to_postgres: Map Trino types to Postgres.
    _resolve_asset_key: Resolve Dagster asset key for events.

Example:
    >>> from phlo_trino.publishing import publish_marts_to_postgres
    >>> stats = publish_marts_to_postgres(
    ...     context=context,
    ...     trino=trino_resource,
    ...     postgres=postgres_resource,
    ...     tables_to_publish={"fct_orders": "gold.fct_orders"},
    ...     data_source="orders_pipeline",
    ... )

"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from psycopg2 import sql
from psycopg2.extras import execute_values

from phlo.hooks import (
    HookCorrelation,
    LineageEventContext,
    LineageEventEmitter,
    PublishEventContext,
    PublishEventEmitter,
    TelemetryEventContext,
    TelemetryEventEmitter,
)
from phlo.config.base import BaseConfig
from phlo.logging import get_logger
from phlo.references import ref
from phlo.utils import dedupe_preserve_order
from phlo_trino._errors import iter_exception_chain
from pydantic import Field

logger = get_logger(__name__)


class TrinoPublishingSettings(BaseConfig):
    """Settings for Trino publish target defaults.

    postgres_mart_schema is the default PostgreSQL schema for published marts.
    """

    postgres_mart_schema: str = Field(default="marts", description="Default mart schema")


@dataclass(frozen=True)
class TablePublishStats:
    """Summary stats for a published table: its copied row count and column count."""

    row_count: int
    column_count: int


def publish_marts_to_target(
    *,
    context: Any,
    trino: Any,
    publish_target: Any,
    tables_to_publish: dict[str, str],
    data_source: str,
    target_schema: str | None = None,
    batch_size: int = 10_000,
) -> dict[str, TablePublishStats]:
    """Copy analytical outputs into a structured publish target.

    Publishes multiple tables from Trino to a configured target system
    (e.g., PostgreSQL) with automatic schema management. tables_to_publish maps
    target table names to source Trino qualified names; batch_size controls the
    number of rows per copy batch. Returns a dictionary mapping target table
    names to their publish statistics.

    Example:
        >>> stats = publish_marts_to_target(
        ...     context=dagster_context,
        ...     trino=trino_resource,
        ...     publish_target=postgres_target,
        ...     tables_to_publish={"fct_orders": "gold.fct_orders"},
        ...     data_source="orders_pipeline",
        ... )
        >>> print(stats["fct_orders"].row_count)
        1000

    """
    postgres, target_system, resolved_schema = _resolve_publish_target(
        publish_target,
        target_schema=target_schema,
    )
    return _publish_marts(
        context=context,
        trino=trino,
        postgres=postgres,
        target_system=target_system,
        tables_to_publish=tables_to_publish,
        data_source=data_source,
        target_schema=resolved_schema,
        batch_size=batch_size,
    )


def publish_marts_to_postgres(
    *,
    context: Any,
    trino: Any,
    postgres: Any,
    tables_to_publish: dict[str, str],
    data_source: str,
    target_schema: str | None = None,
    batch_size: int = 10_000,
) -> dict[str, TablePublishStats]:
    """Copy Trino tables into Postgres and emit publish lifecycle events.

    Publishes tables with full event emission for observability, including
    start/completion/failure events, telemetry metrics, and lineage tracking.
    tables_to_publish maps target table names to source Trino qualified names;
    batch_size controls the number of rows per copy batch; target_schema
    defaults to "marts". Returns a dictionary mapping target table names to
    their publish statistics. Raises RuntimeError when table introspection
    fails after retries; on publishing failure a failure event is emitted
    before the exception is re-raised.

    Example:
        >>> stats = publish_marts_to_postgres(
        ...     context=dagster_context,
        ...     trino=trino_resource,
        ...     postgres=pg_resource,
        ...     tables_to_publish={"fct_orders": "gold.fct_orders"},
        ...     data_source="orders_pipeline",
        ...     target_schema="analytics",
        ... )

    """
    return _publish_marts(
        context=context,
        trino=trino,
        postgres=postgres,
        target_system="postgres",
        tables_to_publish=tables_to_publish,
        data_source=data_source,
        target_schema=target_schema or TrinoPublishingSettings().postgres_mart_schema,
        batch_size=batch_size,
    )


def _resolve_publish_target(
    publish_target: Any,
    *,
    target_schema: str | None,
) -> tuple[Any, str, str]:
    """Resolve publish target wrapper or raw resource into publishing primitives."""
    resource = getattr(publish_target, "resource", publish_target)
    target_system = str(getattr(publish_target, "target_system", "postgres"))
    default_schema = getattr(publish_target, "default_schema", None)
    if not isinstance(default_schema, str) or not default_schema:
        default_schema = TrinoPublishingSettings().postgres_mart_schema
    return resource, target_system, target_schema or default_schema


def _publish_marts(
    *,
    context: Any,
    trino: Any,
    postgres: Any,
    target_system: str,
    tables_to_publish: dict[str, str],
    data_source: str,
    target_schema: str,
    batch_size: int,
) -> dict[str, TablePublishStats]:
    """Shared publish implementation for Postgres-backed publish targets."""
    schema = target_schema
    asset_key = _resolve_asset_key(context, data_source)
    correlation = HookCorrelation(
        run_id=getattr(context, "run_id", None),
        asset_key=asset_key,
        partition_key=_resolve_partition_key(context),
        job_name=getattr(context, "job_name", None),
    )
    emitter = PublishEventEmitter(
        PublishEventContext(
            asset_key=asset_key,
            run_id=correlation.run_id,
            partition_key=correlation.partition_key,
            target_system=target_system,
            tables=tables_to_publish,
            tags={"source": data_source, "target": target_system},
            correlation=correlation,
        )
    )
    telemetry = TelemetryEventEmitter(
        TelemetryEventContext(
            tags={"source": data_source, "target": target_system},
            correlation=correlation,
        )
    )
    lineage = LineageEventEmitter(
        LineageEventContext(
            tags={"source": data_source, "target": target_system}, correlation=correlation
        )
    )
    start_time = time.time()

    logger.info(
        "trino_publish_started",
        data_source=data_source,
        target_schema=schema,
        target_system=target_system,
        table_count=len(tables_to_publish),
        batch_size=batch_size,
        asset_key=asset_key,
    )
    emitter.emit_start()

    stats: dict[str, TablePublishStats] = {}
    try:
        _ensure_schema(postgres, schema)
        for target_table, source_table in tables_to_publish.items():
            logger.info(
                "trino_publish_table_started",
                source_table=source_table,
                target_schema=schema,
                target_system=target_system,
                target_table=target_table,
                batch_size=batch_size,
            )
            row_count, column_count = _copy_table(
                trino=trino,
                postgres=postgres,
                source_table=source_table,
                target_schema=schema,
                target_table=target_table,
                batch_size=batch_size,
            )
            stats[target_table] = TablePublishStats(
                row_count=row_count,
                column_count=column_count,
            )
            logger.info(
                "trino_publish_table_completed",
                source_table=source_table,
                target_schema=schema,
                target_system=target_system,
                target_table=target_table,
                row_count=row_count,
                column_count=column_count,
            )
        emitter.emit_end(
            status="success",
            metrics={
                "tables": {
                    name: {"row_count": s.row_count, "column_count": s.column_count}
                    for name, s in stats.items()
                }
            },
        )
        elapsed = time.time() - start_time
        total_rows = sum(item.row_count for item in stats.values())
        total_columns = sum(item.column_count for item in stats.values())
        telemetry.emit_metric(name="publish.tables", value=len(stats), unit="tables")
        telemetry.emit_metric(name="publish.rows_total", value=total_rows, unit="rows")
        telemetry.emit_metric(name="publish.columns_total", value=total_columns, unit="columns")
        telemetry.emit_metric(name="publish.duration_seconds", value=elapsed, unit="seconds")
        edges = [
            (source_table, f"{schema}.{target_table}")
            for target_table, source_table in tables_to_publish.items()
        ]
        if edges:
            lineage.emit_edges(
                edges=edges,
                asset_keys=[edge[1] for edge in edges],
                metadata={"source_system": "trino", "target_system": target_system},
                operation_id=(
                    f"publish:{data_source}:{schema}:{','.join(sorted(tables_to_publish))}"
                ),
            )
        logger.info(
            "trino_publish_completed",
            data_source=data_source,
            target_schema=schema,
            target_system=target_system,
            table_count=len(stats),
            total_rows=total_rows,
            total_columns=total_columns,
            elapsed_seconds=elapsed,
        )
        return stats
    except Exception as exc:
        elapsed = time.time() - start_time
        logger.exception(
            "trino_publish_failed",
            data_source=data_source,
            target_schema=schema,
            target_system=target_system,
            table_count=len(tables_to_publish),
            elapsed_seconds=elapsed,
        )
        emitter.emit_end(status="failure", error=str(exc))
        telemetry.emit_log(
            name="publish.failure",
            level="error",
            payload={"error": str(exc), "elapsed_seconds": elapsed},
        )
        raise


def _resolve_partition_key(context: Any) -> str | None:
    """Resolve a partition key from a neutral runtime context hint."""
    has_partition_key = getattr(context, "has_partition_key", None)
    if has_partition_key is False:
        return None

    partition_key = getattr(context, "partition_key", None)
    return partition_key if isinstance(partition_key, str) and partition_key else None


def _ensure_schema(postgres: Any, schema: str) -> None:
    """Create the target schema if it is missing."""
    with postgres.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
    postgres.commit()


def _split_trino_qualified_name(name: str) -> list[tuple[str, bool]]:
    """Split a Trino qualified name into parts and track quoted identifiers."""
    parts: list[tuple[str, bool]] = []
    buffer: list[str] = []
    in_quotes = False
    part_quoted = False
    index = 0
    while index < len(name):
        char = name[index]
        if char == '"':
            if in_quotes and index + 1 < len(name) and name[index + 1] == '"':
                buffer.append('"')
                index += 1
            else:
                in_quotes = not in_quotes
                part_quoted = True
        elif char == "." and not in_quotes:
            part = "".join(buffer)
            if not part:
                raise ValueError("Trino table name has an empty identifier part.")
            parts.append((part, part_quoted))
            buffer = []
            part_quoted = False
        elif not in_quotes and char.isspace():
            pass
        else:
            buffer.append(char)
        index += 1
    if in_quotes:
        raise ValueError("Trino table name has an unterminated quoted identifier.")
    part = "".join(buffer)
    if not part:
        raise ValueError("Trino table name has an empty identifier part.")
    parts.append((part, part_quoted))
    return parts


def _quote_trino_identifier(identifier: str, *, was_quoted: bool) -> str:
    """Quote a single Trino identifier, preserving case when already quoted."""
    if not identifier:
        raise ValueError("Trino identifier cannot be empty.")
    normalized = identifier if was_quoted else identifier.lower()
    escaped = normalized.replace('"', '""')
    return f'"{escaped}"'


def _quote_trino_qualified_name(name: str) -> str:
    """Quote a fully qualified Trino table name for safe SQL usage."""
    parts = _split_trino_qualified_name(name)
    return ".".join(
        _quote_trino_identifier(part, was_quoted=was_quoted) for part, was_quoted in parts
    )


def _copy_table(
    *,
    trino: Any,
    postgres: Any,
    source_table: str,
    target_schema: str,
    target_table: str,
    batch_size: int,
) -> tuple[int, int]:
    """Copy a single Trino table into Postgres and return row/column counts."""
    resolved_source_table = _resolve_source_table_reference(source_table)
    columns, source_table_ref = _describe_trino_table(trino, resolved_source_table)
    column_defs = [
        sql.SQL("{} {}").format(sql.Identifier(name), sql.SQL(pg_type))
        for name, pg_type, _expr in columns
    ]
    column_idents = [sql.Identifier(name) for name, _pg_type, _expr in columns]
    select_exprs = [expr for _name, _pg_type, expr in columns]

    with postgres.cursor() as cursor:
        cursor.execute(
            sql.SQL("DROP TABLE IF EXISTS {}.{}").format(
                sql.Identifier(target_schema),
                sql.Identifier(target_table),
            )
        )
        cursor.execute(
            sql.SQL("CREATE TABLE {}.{} ({})").format(
                sql.Identifier(target_schema),
                sql.Identifier(target_table),
                sql.SQL(", ").join(column_defs),
            )
        )
    postgres.commit()

    insert_query = sql.SQL("INSERT INTO {}.{} ({}) VALUES %s").format(
        sql.Identifier(target_schema),
        sql.Identifier(target_table),
        sql.SQL(", ").join(column_idents),
    )

    row_count = 0
    with trino.cursor() as trino_cursor:
        trino_cursor.execute(f"SELECT {', '.join(select_exprs)} FROM {source_table_ref}")
        with postgres.cursor() as pg_cursor:
            while True:
                rows = trino_cursor.fetchmany(batch_size)
                if not rows:
                    break
                execute_values(pg_cursor, insert_query, rows, page_size=batch_size)
                row_count += len(rows)
    postgres.commit()

    return row_count, len(columns)


def _resolve_source_table_reference(source_table: str) -> str:
    """Resolve logical publishing table tokens into concrete Trino relations."""
    if not source_table.startswith("ref:"):
        return source_table
    model_name = source_table.removeprefix("ref:").strip()
    if not model_name:
        raise ValueError("Publishing table reference 'ref:' must include a dbt model name.")
    return ref(model_name).render()


def _trino_table_ref_candidates(name: str) -> list[str]:
    """Build likely-valid Trino table references for introspection queries."""
    parts = _split_trino_qualified_name(name)
    quoted_all = ".".join(
        _quote_trino_identifier(part, was_quoted=was_quoted) for part, was_quoted in parts
    )
    plain_all = ".".join(part for part, _was_quoted in parts)

    candidates: list[str] = [quoted_all, plain_all]
    if len(parts) == 3:
        schema_table_parts = parts[1:]
        quoted_schema_table = ".".join(
            _quote_trino_identifier(part, was_quoted=was_quoted)
            for part, was_quoted in schema_table_parts
        )
        plain_schema_table = ".".join(part for part, _was_quoted in schema_table_parts)
        candidates.extend([quoted_schema_table, plain_schema_table])

    return dedupe_preserve_order(candidates)


def _describe_trino_table(trino: Any, source_table: str) -> tuple[list[tuple[str, str, str]], str]:
    """Return column metadata and the resolved source table reference."""
    last_error: Exception | None = None
    table_refs = _trino_table_ref_candidates(source_table)
    max_attempts = 5
    retry_delay_seconds = 1.0
    non_retryable_candidates: set[str] = set()

    for attempt in range(max_attempts):
        saw_retryable_error = False
        for source_table_ref in table_refs:
            if source_table_ref in non_retryable_candidates:
                continue
            for query in (f"DESCRIBE {source_table_ref}", f"SHOW COLUMNS FROM {source_table_ref}"):
                try:
                    with trino.cursor() as cursor:
                        cursor.execute(query)
                        rows = cursor.fetchall()
                    columns = [
                        (str(row[0]), *_trino_type_to_postgres(str(row[0]), str(row[1])))
                        for row in rows
                        if row and len(row) >= 2
                    ]
                    if columns:
                        return columns, source_table_ref
                except Exception as exc:  # noqa: BLE001 - fallback across valid table refs
                    last_error = exc
                    if _is_retryable_introspection_error(exc):
                        logger.warning(
                            "trino_introspection_retryable_error",
                            source_table=source_table,
                            source_table_ref=source_table_ref,
                            query=query,
                            attempt=attempt + 1,
                            max_attempts=max_attempts,
                        )
                        saw_retryable_error = True
                        continue
                    logger.warning(
                        "trino_introspection_non_retryable_error",
                        source_table=source_table,
                        source_table_ref=source_table_ref,
                        query=query,
                        attempt=attempt + 1,
                        max_attempts=max_attempts,
                    )
                    non_retryable_candidates.add(source_table_ref)
                    break
        if attempt < max_attempts - 1 and saw_retryable_error:
            delay = retry_delay_seconds * (attempt + 1)
            logger.info(
                "trino_introspection_retry_scheduled",
                source_table=source_table,
                attempt=attempt + 1,
                max_attempts=max_attempts,
                delay_seconds=delay,
            )
            time.sleep(delay)
            continue
        break

    if last_error is not None:
        logger.error(
            "trino_introspection_failed",
            source_table=source_table,
            attempts=max_attempts,
            candidate_count=len(table_refs),
        )
        raise RuntimeError(f"Unable to introspect Trino table '{source_table}'") from last_error
    logger.error(
        "trino_introspection_failed_no_error",
        source_table=source_table,
        attempts=max_attempts,
        candidate_count=len(table_refs),
    )
    raise RuntimeError(f"Unable to introspect Trino table '{source_table}'")


def _is_retryable_introspection_error(exc: Exception) -> bool:
    retryable_error_names = {
        "table_not_found",
        "schema_not_found",
        "server_starting_up",
        "catalog_not_found",
    }
    retryable_error_types = {"external", "internal_error", "insufficient_resources"}

    for error in iter_exception_chain(exc):
        if _is_trino_user_error(error):
            error_name = getattr(error, "error_name", None)
            if error_name and str(error_name).lower() in retryable_error_names:
                return True
            error_type = getattr(error, "error_type", None)
            if error_type and str(error_type).lower() in retryable_error_types:
                return True
        error_name = getattr(error, "error_name", None)
        if error_name and str(error_name).lower() in retryable_error_names:
            return True
        error_type = getattr(error, "error_type", None)
        if error_type and str(error_type).lower() in retryable_error_types:
            return True

    message = str(exc).lower()
    retryable_snippets = (
        "table_not_found",
        "does not exist",
        "no such table",
        "server_starting_up",
        "connection refused",
        "temporarily unavailable",
        "timed out",
    )
    return any(snippet in message for snippet in retryable_snippets)


def _is_trino_user_error(error: BaseException) -> bool:
    """Return whether an optional Trino client exception is present."""
    try:
        from trino.exceptions import TrinoUserError
    except Exception:  # noqa: BLE001 - optional dependency
        return False
    return isinstance(error, TrinoUserError)


_TRINO_TO_PG_SIMPLE: dict[str, str] = {
    "timestamptz": "timestamptz",
    "varchar": "text",
    "char": "text",
    "string": "text",
    "bigint": "bigint",
    "integer": "integer",
    "int": "integer",
    "smallint": "smallint",
    "double": "double precision",
    "double precision": "double precision",
    "real": "real",
    "boolean": "boolean",
    "date": "date",
    "timestamp": "timestamp",
    "numeric": "numeric",
}

_TRINO_JSON_TYPES = {"json", "array", "map", "row"}


def _trino_type_to_postgres(column: str, trino_type: str) -> tuple[str, str]:
    """Map a Trino column type to a Postgres type and select expression."""
    column_ref = _quote_trino_identifier(column, was_quoted=True)
    normalized = trino_type.lower()
    base = normalized.split("(")[0].strip()

    if "timestamp" in normalized and "time zone" in normalized:
        return "timestamptz", column_ref
    if base in _TRINO_TO_PG_SIMPLE:
        return _TRINO_TO_PG_SIMPLE[base], column_ref
    if base.startswith("decimal"):
        return "numeric", column_ref
    if base in _TRINO_JSON_TYPES:
        return "jsonb", f"CAST({column_ref} AS JSON)"
    return "text", f"CAST({column_ref} AS VARCHAR)"


def _resolve_asset_key(context: Any, data_source: str) -> str | None:
    """Resolve the Dagster asset key for publish events."""
    asset_key = getattr(context, "asset_key", None)
    if asset_key is None:
        return f"publish_{data_source}_marts"
    if hasattr(asset_key, "path") and asset_key.path:
        return "/".join(str(part) for part in asset_key.path)
    return str(asset_key)
