"""Trino resource for executing queries and managing connections.

This module provides the TrinoResource class for interacting with Trino,
including connection management, query execution, and wait-for-readiness
functionality with automatic retry logic.

Classes:
    TrinoResource: Resource wrapper for Trino connections and queries.
    _ConfigFacade: Backward-compatible config shim for test patching.

Constants:
    TRINO_QUERY_ENGINE_SUPPORT: Capability support flags for query engine.

Functions:
    _is_transient_trino_error: Check if an exception indicates transient error.

Example:
    >>> from phlo_trino.resource import TrinoResource
    >>> trino = TrinoResource()
    >>> results = trino.execute("SELECT * FROM iceberg.my_schema.my_table")
    >>> trino.wait_ready(timeout=30.0)


Implements the Trino capability resource; nothing outside this package imports it.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
import re
import time
from typing import Any, Iterable

import pandas as pd
from trino.dbapi import connect

from phlo.capabilities import (
    CapabilitySupport,
    MaintenanceExecutionError,
    MaintenanceExecutionPhase,
    MaintenancePreconditionError,
    QueryPreviewResult,
    RuntimeContext,
    resolve_runtime_ref,
)
from phlo.logging import get_logger
from phlo.references import LogicalRelation, quote_identifier
from phlo_trino._errors import iter_exception_chain
from phlo_trino.settings import get_settings as get_trino_settings
from phlo_trino.type_mapping import apply_schema_types

logger = get_logger(__name__)

_MAINTENANCE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CATALOG_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_OWNED_WAP_REF_PREFIX = "pipeline-run-"

TRINO_QUERY_ENGINE_SUPPORT = CapabilitySupport(
    supports_refs=True,
    supports_time_travel=True,
)


class _ConfigFacade:
    """Backward-compatible config shim for tests patching `phlo_trino.resource.config`."""

    @property
    def trino_host(self) -> str:
        """Return the configured Trino service hostname."""
        return get_trino_settings().trino_host

    @property
    def trino_port(self) -> int:
        """Return the configured Trino HTTP port."""
        return get_trino_settings().trino_port

    @property
    def trino_catalog(self) -> str:
        """Return the configured default Trino catalog name."""
        return get_trino_settings().trino_catalog

    @property
    def default_catalog_ref(self) -> str:
        """Return the configured branch or tag used when no explicit ref is set."""
        return get_trino_settings().trino_default_ref


config = _ConfigFacade()


@dataclass
class TrinoResource:
    """Resource wrapper for Trino connections and query execution."""

    host: str | None = None
    port: int | None = None
    user: str = "dagster"
    catalog: str | None = None
    ref: str | None = None
    runtime: RuntimeContext | None = None

    def _resolved_ref(self) -> str | None:
        """Resolve the effective ref for catalog routing, falling back to the default."""
        return resolve_runtime_ref(
            self.runtime,
            support=TRINO_QUERY_ENGINE_SUPPORT,
            default_ref=self.ref or config.default_catalog_ref,
        )

    def _resolved_catalog(self) -> str:
        """Resolve the connection catalog, switching to a per-ref catalog off main."""
        base_catalog = self.catalog or config.trino_catalog
        ref = self._resolved_ref()
        # Non-main refs route to a dedicated per-ref catalog created by
        # _provision_catalog; its name must keep the {base}_{ref} convention.
        if ref and ref != "main":
            return f"{base_catalog}_{ref}"
        return base_catalog

    @staticmethod
    def _quote_catalog_identifier(name: str) -> str:
        """Return a validated Trino catalog identifier for dynamic-catalog SQL."""
        if not _CATALOG_IDENTIFIER.fullmatch(name):
            raise ValueError(f"Invalid Trino catalog identifier: {name!r}")
        return f'"{name}"'

    @staticmethod
    def _quote_sql_literal(value: str) -> str:
        """Return a SQL string literal without allowing property-value injection."""
        return "'" + value.replace("'", "''") + "'"

    def _dynamic_catalog_properties(self, ref: str) -> dict[str, str]:
        """Return the direct Nessie catalog properties for one exact reference."""
        s3_endpoint = os.environ.get("S3_ENDPOINT", "http://minio:9000")
        s3_region = os.environ.get("AWS_REGION", "us-east-1")
        return {
            "iceberg.catalog.type": "nessie",
            "iceberg.nessie-catalog.uri": "http://nessie:19120/api/v2",
            "iceberg.nessie-catalog.ref": ref,
            "iceberg.nessie-catalog.default-warehouse-dir": "s3://lake/warehouse",
            "fs.native-s3.enabled": "true",
            "s3.endpoint": s3_endpoint,
            "s3.path-style-access": "true",
            "s3.region": s3_region,
        }

    def _provision_catalog(self, catalog: str, ref: str) -> None:
        """Create the deterministic in-memory catalog for a Nessie reference.

        ``CREATE CATALOG IF NOT EXISTS`` makes concurrent readers of the same
        run reference converge on one catalog without deleting or replacing a
        catalog owned by another run. S3 credentials stay in the Trino service
        environment and are never included in this SQL statement.
        """
        quoted_catalog = self._quote_catalog_identifier(catalog)
        properties = self._dynamic_catalog_properties(ref)
        rendered_properties = ", ".join(
            f'"{key}" = {self._quote_sql_literal(value)}' for key, value in properties.items()
        )
        statement = (
            f"CREATE CATALOG IF NOT EXISTS {quoted_catalog} USING iceberg "
            f"WITH ({rendered_properties})"
        )
        bootstrap = connect(
            host=self.host or config.trino_host,
            port=self.port or config.trino_port,
            user=self.user,
            catalog="system",
            schema="runtime",
        )
        try:
            with bootstrap.cursor() as cursor:
                cursor.execute(statement)
        finally:
            bootstrap.close()

    def _provision_resolved_catalog(self) -> None:
        """Provision the active WAP catalog before connecting to it."""
        base_catalog = self.catalog or config.trino_catalog
        ref = self._resolved_ref()
        if (
            base_catalog != config.trino_catalog
            or ref is None
            or not ref.startswith(_OWNED_WAP_REF_PREFIX)
        ):
            return
        self.provision_ref_query_catalog(ref)

    def provision_ref_query_catalog(self, ref: str) -> str:
        """Provision the deterministic query catalog owned by a WAP run ref."""
        if not ref.startswith(_OWNED_WAP_REF_PREFIX):
            raise ValueError("Only owned pipeline-run catalogs can be provisioned")
        base_catalog = self.catalog or config.trino_catalog
        if base_catalog != config.trino_catalog:
            raise ValueError("Only the configured Nessie catalog can be provisioned")
        catalog = f"{base_catalog}_{ref}"
        self._provision_catalog(catalog, ref)
        return catalog

    def drop_ref_query_catalog(self, ref: str) -> None:
        """Remove this resource's owned WAP query catalog after explicit cleanup.

        The deterministic name must be derived from this resource and the
        supplied WAP ref, so callers cannot drop the shared main catalog or an
        unrelated catalog.
        """
        if not ref.startswith(_OWNED_WAP_REF_PREFIX):
            raise ValueError("Only owned pipeline-run catalogs can be removed")
        base_catalog = self.catalog or config.trino_catalog
        if base_catalog != config.trino_catalog:
            raise ValueError("Only the configured Nessie catalog can be removed")
        catalog = f"{base_catalog}_{ref}"
        quoted_catalog = self._quote_catalog_identifier(catalog)
        bootstrap = connect(
            host=self.host or config.trino_host,
            port=self.port or config.trino_port,
            user=self.user,
            catalog="system",
            schema="runtime",
        )
        try:
            with bootstrap.cursor() as cursor:
                cursor.execute(f"DROP CATALOG IF EXISTS {quoted_catalog}")
        finally:
            bootstrap.close()

    def get_connection(self, schema: str | None = None):
        """Open a DB-API connection to Trino, provisioning any owned WAP catalog first.

        `schema` optionally scopes the connection.
        """
        self._provision_resolved_catalog()
        return connect(
            host=self.host or config.trino_host,
            port=self.port or config.trino_port,
            user=self.user,
            catalog=self._resolved_catalog(),
            schema=schema,
        )

    def for_ref(self, ref: str) -> TrinoResource:
        """Return a resource configured for the requested Iceberg ref."""
        return TrinoResource(
            host=self.host,
            port=self.port,
            user=self.user,
            catalog=self.catalog,
            ref=ref,
            runtime=None,
        )

    @contextmanager
    def cursor(self, schema: str | None = None):
        """Yield an active Trino cursor, closing cursor and connection on exit."""
        conn = self.get_connection(schema=schema)
        cursor = None
        try:
            cursor = conn.cursor()
            yield cursor
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                conn.close()

    def execute(self, sql: str, params: Iterable[object] | None = None, schema: str | None = None):
        """Execute SQL with optional positional params; return rows, or [] without a result set."""
        params = list(params or [])
        with self.cursor(schema=schema) as cursor:
            cursor.execute(sql, params)
            if cursor.description is None:
                return []
            return cursor.fetchall()

    def preview(
        self, relation: str, *, limit: int, offset: int = 0, schema: str | None = None
    ) -> QueryPreviewResult:
        """Return one bounded page, fetching one additional row to detect continuation."""
        page_size = max(1, min(limit, 500))
        statement = f"SELECT * FROM {relation}"
        if offset > 0:
            statement = f"{statement} OFFSET {offset}"
        statement = f"{statement} LIMIT {page_size + 1}"
        with self.cursor(schema=schema) as cursor:
            cursor.execute(statement, [])
            description = cursor.description or []
            columns = [str(column[0]) for column in description]
            column_types = [
                str(column[1]) if len(column) > 1 else "unknown" for column in description
            ]
            raw_rows = cursor.fetchall()
        rows = [dict(zip(columns, row, strict=False)) for row in raw_rows[:page_size]]
        return QueryPreviewResult(
            columns=columns,
            column_types=column_types,
            rows=rows,
            has_more=len(raw_rows) > page_size,
        )

    def compact_table(
        self,
        *,
        table_name: str,
        ref: str,
        expected_revision: str | int | None = None,
        operation_id: str | None = None,
    ) -> dict[str, object]:
        """Execute table compaction against this resource's selected ref.

        The requested ref is checked against the connection catalog before any
        SQL is sent, so a caller cannot accidentally compact the default branch
        with a policy intended for another branch.
        """
        parts = table_name.split(".")
        if len(parts) != 2 or any(not _MAINTENANCE_IDENTIFIER.fullmatch(part) for part in parts):
            raise MaintenancePreconditionError(
                "Compaction table_name must be a namespace.table identifier containing only "
                "letters, numbers, and underscores."
            )
        requested_ref = ref or "main"
        effective_ref = self._resolved_ref() or "main"
        if effective_ref != requested_ref:
            raise MaintenancePreconditionError(
                f"Trino executor is configured for ref {effective_ref!r}, "
                f"but compaction requested {requested_ref!r}"
            )
        history_relation = ".".join(
            quote_identifier(part) for part in (*parts[:-1], f"{parts[-1]}$history")
        )
        try:
            snapshot_rows = self.execute(
                f"SELECT snapshot_id FROM {history_relation} "
                "WHERE is_current_ancestor ORDER BY made_current_at DESC LIMIT 1"
            )
        except Exception as exc:  # noqa: BLE001 - no mutation was submitted
            raise MaintenanceExecutionError(MaintenanceExecutionPhase.PREFLIGHT, exc) from exc
        current_snapshot_id = int(snapshot_rows[0][0]) if snapshot_rows else None
        if expected_revision != current_snapshot_id:
            raise MaintenancePreconditionError(
                "Iceberg snapshot changed before compaction: "
                f"expected {expected_revision!r}, current {current_snapshot_id!r}"
            )
        sql = f"ALTER TABLE {'.'.join(quote_identifier(part) for part in parts)} EXECUTE optimize"
        logger.info(
            "trino_iceberg_compaction_requested",
            table_name=table_name,
            ref=effective_ref,
            catalog=self._resolved_catalog(),
            operation_id=operation_id,
        )
        try:
            rows = self.execute(sql)
        except Exception as exc:  # noqa: BLE001 - DDL submission outcome is ambiguous
            raise MaintenanceExecutionError(MaintenanceExecutionPhase.SUBMISSION, exc) from exc
        return {
            "table_name": table_name,
            "ref": effective_ref,
            "catalog": self._resolved_catalog(),
            "sql": sql,
            "rows": rows,
        }

    def expire_snapshots_table(
        self,
        *,
        table_name: str,
        ref: str,
        expected_revision: str | int | None,
        retention_hours: int,
        retain_last: int,
        operation_id: str | None = None,
    ) -> dict[str, object]:
        """Submit guarded, non-atomic Iceberg snapshot expiry through Trino.

        The history read protects the selected catalog reference immediately
        before submission, but Trino's threshold procedure cannot bind an exact
        deletion set, serialize concurrent work on other Nessie references, or
        enforce the caller's retain-last count.
        """
        parts = table_name.split(".")
        if len(parts) != 2 or any(not _MAINTENANCE_IDENTIFIER.fullmatch(part) for part in parts):
            raise MaintenancePreconditionError(
                "Snapshot expiry table_name must be a namespace.table identifier containing only "
                "letters, numbers, and underscores."
            )
        if retention_hours <= 0 or retain_last < 1:
            raise MaintenancePreconditionError(
                "Snapshot expiry requires positive retention_hours and retain_last."
            )
        requested_ref = ref or "main"
        effective_ref = self._resolved_ref() or "main"
        if effective_ref != requested_ref:
            raise MaintenancePreconditionError(
                f"Trino executor is configured for ref {effective_ref!r}, "
                f"but snapshot expiry requested {requested_ref!r}"
            )
        history_relation = ".".join(
            quote_identifier(part) for part in (*parts[:-1], f"{parts[-1]}$history")
        )
        try:
            snapshot_rows = self.execute(
                f"SELECT snapshot_id FROM {history_relation} "
                "WHERE is_current_ancestor ORDER BY made_current_at DESC LIMIT 1"
            )
        except Exception as exc:  # noqa: BLE001 - no mutation was submitted
            raise MaintenanceExecutionError(MaintenanceExecutionPhase.PREFLIGHT, exc) from exc
        current_snapshot_id = int(snapshot_rows[0][0]) if snapshot_rows else None
        if expected_revision != current_snapshot_id:
            raise MaintenancePreconditionError(
                "Iceberg snapshot changed before snapshot expiry: "
                f"expected {expected_revision!r}, current {current_snapshot_id!r}"
            )
        relation = ".".join(quote_identifier(part) for part in parts)
        sql = (
            f"ALTER TABLE {relation} EXECUTE expire_snapshots("
            f"retention_threshold => '{retention_hours}h')"
        )
        logger.info(
            "trino_iceberg_snapshot_expiry_requested",
            table_name=table_name,
            ref=effective_ref,
            catalog=self._resolved_catalog(),
            retention_hours=retention_hours,
            retain_last=retain_last,
            operation_id=operation_id,
        )
        try:
            rows = self.execute(sql)
        except Exception as exc:  # noqa: BLE001 - DDL submission outcome is ambiguous
            raise MaintenanceExecutionError(MaintenanceExecutionPhase.SUBMISSION, exc) from exc
        return {
            "table_name": table_name,
            "ref": effective_ref,
            "catalog": self._resolved_catalog(),
            "sql": sql,
            "preflight": {"snapshot_id": current_snapshot_id},
            "retain_last": {
                "requested": retain_last,
                "enforced": False,
                "reason": "trino_expire_snapshots_supports_retention_threshold_only",
            },
            "rows": rows,
        }

    def read_dataframe(
        self,
        query: str | LogicalRelation,
        params: Iterable[object] | None = None,
        *,
        schema: str | None = None,
        schema_class: type[Any] | None = None,
    ) -> pd.DataFrame:
        """Execute a read query and return results as a pandas DataFrame.

        `query` is a SQL string or a logical relation read with ``SELECT *``;
        `schema_class`, when given, applies lightweight Pandera-style type
        coercion. RuntimeError carries SQL/relation context when execution
        fails.
        """
        sql = self._read_dataframe_sql(query)
        params_list = list(params or [])
        context = _query_context(query, sql)
        try:
            with self.cursor(schema=schema) as cursor:
                cursor.execute(sql, params_list)
                columns = _columns_from_description(cursor.description)
                rows = [] if cursor.description is None else cursor.fetchall()
        except Exception as exc:  # noqa: BLE001 - attach query context for workflow authors
            raise RuntimeError(f"Trino query failed for {context}: {exc}") from exc

        try:
            frame = pd.DataFrame(rows, columns=columns)
            if schema_class is not None:
                frame = apply_schema_types(frame, schema_class)
            return frame
        except Exception as exc:  # noqa: BLE001 - attach query context for workflow authors
            schema_context = (
                f" with schema {schema_class.__name__}" if schema_class is not None else ""
            )
            raise RuntimeError(
                f"Trino DataFrame conversion failed for {context}{schema_context}: {exc}"
            ) from exc

    def read_table(
        self,
        table: str | LogicalRelation,
        *,
        columns: Iterable[str] | None = None,
        limit: int | None = None,
        params: Iterable[object] | None = None,
        schema: str | None = None,
        schema_class: type[Any] | None = None,
    ) -> pd.DataFrame:
        """Read a table or logical relation into a pandas DataFrame.

        Selects all columns unless `columns` narrows them; `limit` bounds the
        row count and must be non-negative.
        """
        selected = _render_columns(columns)
        relation = _render_table(table)
        sql = f"SELECT {selected} FROM {relation}"
        if limit is not None:
            if limit < 0:
                raise ValueError("limit must be non-negative")
            sql = f"{sql} LIMIT {limit}"
        return self.read_dataframe(sql, params=params, schema=schema, schema_class=schema_class)

    def _read_dataframe_sql(self, query: str | LogicalRelation) -> str:
        if isinstance(query, LogicalRelation):
            return f"SELECT * FROM {query.render()}"
        return query

    def wait_ready(
        self,
        *,
        timeout: float = 60.0,
        interval: float = 1.0,
        schema: str | None = None,
    ) -> None:
        """Wait for Trino to accept queries, retrying on startup/connection errors.

        Polls with "SELECT 1" every `interval` seconds until `timeout`
        expires. Transient errors are retried; non-transient ones raise
        immediately, and TimeoutError is raised once the deadline passes.

        Example:
            >>> trino = TrinoResource()
            >>> trino.wait_ready(timeout=30.0, interval=2.0)
        """
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        interval = max(interval, 0.0)
        while time.monotonic() < deadline:
            try:
                self.execute("SELECT 1", schema=schema)
                logger.info(
                    "trino_wait_ready_succeeded",
                    host=self.host or config.trino_host,
                    port=self.port or config.trino_port,
                    schema=schema,
                )
                return
            except Exception as exc:  # noqa: BLE001 - surface real error after timeout
                if not _is_transient_trino_error(exc):
                    logger.exception(
                        "trino_wait_ready_non_transient_error",
                        host=self.host or config.trino_host,
                        port=self.port or config.trino_port,
                        schema=schema,
                    )
                    raise
                last_error = exc
                logger.debug(
                    "trino_wait_ready_retry",
                    host=self.host or config.trino_host,
                    port=self.port or config.trino_port,
                    schema=schema,
                    retry_interval_seconds=interval,
                )
                time.sleep(interval)
        logger.error(
            "trino_wait_ready_timeout",
            host=self.host or config.trino_host,
            port=self.port or config.trino_port,
            schema=schema,
            timeout_seconds=timeout,
        )
        raise TimeoutError(f"Trino not ready after {timeout:.1f}s") from last_error


def _is_transient_trino_error(exc: Exception) -> bool:
    """Return True when the exception chain looks transient enough to retry."""
    for error in iter_exception_chain(exc):
        message = str(error).lower()
        if "server_starting_up" in message:
            return True
        if any(
            snippet in message
            for snippet in (
                "connection refused",
                "failed to establish",
                "max retries exceeded",
                "temporarily unavailable",
                "connection reset",
                "connection aborted",
                "timed out",
            )
        ):
            return True
        # errno values are Linux socket codes: ECONNRESET, ECONNREFUSED, EHOSTUNREACH.
        errno = getattr(error, "errno", None)
        if errno in {104, 111, 113}:
            return True
        error_code = getattr(error, "error_code", None)
        if error_code:
            error_name = getattr(error_code, "name", None)
            if error_name and "server_starting_up" in str(error_name).lower():
                return True
            error_value = getattr(error_code, "code", None)
            if error_value and "server_starting_up" in str(error_value).lower():
                return True
        error_name = getattr(error, "error_name", None)
        if error_name and "server_starting_up" in str(error_name).lower():
            return True
        module_name = getattr(error.__class__, "__module__", "")
        class_name = error.__class__.__name__.lower()
        if module_name.startswith("urllib3") or module_name.startswith("requests"):
            return True
        if "connectionerror" in class_name or "connection" in class_name:
            return True
    return False


def _columns_from_description(description: Any) -> list[str] | None:
    if description is None:
        return None
    columns: list[str] = []
    for column in description:
        name = getattr(column, "name", None)
        if name is None:
            name = column[0]
        columns.append(str(name))
    return columns


def _render_columns(columns: Iterable[str] | None) -> str:
    if columns is None:
        return "*"
    rendered = list(columns)
    if not rendered:
        return "*"
    return ", ".join("*" if column == "*" else quote_identifier(column) for column in rendered)


def _render_table(table: str | LogicalRelation) -> str:
    if isinstance(table, LogicalRelation):
        return table.render()
    return ".".join(quote_identifier(part) for part in table.split("."))


def _query_context(query: str | LogicalRelation, sql: str) -> str:
    if isinstance(query, LogicalRelation):
        return f"relation {query!r}"
    return f"SQL {_sanitize_sql_context(sql)!r}"


def _sanitize_sql_context(sql: str, *, max_length: int = 300) -> str:
    sanitized: list[str] = []
    in_string = False
    index = 0
    while index < len(sql):
        char = sql[index]
        if in_string:
            if char == "'":
                if index + 1 < len(sql) and sql[index + 1] == "'":
                    index += 2
                    continue
                in_string = False
            index += 1
            continue
        if char == "'":
            sanitized.append("'?'")
            in_string = True
        else:
            sanitized.append(char)
        index += 1
    rendered = "".join(sanitized)
    if len(rendered) > max_length:
        return f"{rendered[: max_length - 3]}..."
    return rendered
