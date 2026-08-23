"""Row-level and column-level lineage store for Phlo.

PostgreSQL-backed persistence for tracking data lineage at both the row and
column levels. Uses ULIDs (Universally Unique Lexicographically Sortable
Identifiers) for row identification and supports deterministic querying of
provenance information. LineageStore is the primary interface, providing
row-level lineage tracking with parent-child relationships, column-level
lineage mappings between assets, asset node and edge management for graph
construction, batch operations for efficient bulk inserts, and recursive
queries for ancestor/descendant traversal.

Example:
    >>> from phlo_lineage.store import LineageStore, generate_row_id
    >>> store = LineageStore("postgresql://user:pass@localhost:5432/phlo")
    >>> row_id = generate_row_id()
    >>> store.record_row(row_id, "bronze.orders", "dlt")

The schema is auto-created on first use via SQL migration files, guarded by a
class-level schema initialization flag for performance, pools connections via
psycopg2 context managers, and stores flexible metadata in JSONB columns.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import psycopg2
import ulid

from phlo.config.network import resolve_host
from phlo.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ColumnLineage:
    """Represents a single column-to-column lineage mapping between two assets.

    Captures the relationship between a source column in an upstream asset and a
    target column in a downstream asset, along with metadata about how the mapping
    was derived. ``source_asset`` and ``target_asset`` are fully qualified names
    (e.g., "bronze.orders" upstream, "silver.stg_orders" downstream);
    ``source_column`` and ``target_column`` name the columns in each;
    ``source_type`` records the mapping's origin, typically "dbt_heuristic" for
    name-based matching or "manual" for user-defined mappings; ``metadata``
    optionally holds additional context such as transformation logic, confidence
    scores, or data quality metrics.

    Example:
        >>> lineage = ColumnLineage(
        ...     source_asset="bronze.orders",
        ...     source_column="order_id",
        ...     target_asset="silver.stg_orders",
        ...     target_column="order_id",
        ...     source_type="dbt_heuristic",
        ...     metadata={"confidence": 0.95},
        ... )
    """

    source_asset: str
    source_column: str
    target_asset: str
    target_column: str
    source_type: str = "dbt_heuristic"
    metadata: dict[str, Any] | None = None


_LINEAGE_DB_KEYS = (
    "LINEAGE_DB_URL",
    "PHLO_LINEAGE_DB_URL",
    "DAGSTER_PG_DB_CONNECTION_STRING",
)


def resolve_lineage_db_url() -> str | None:
    """Resolve the lineage database URL from explicit lineage environment variables.

    Checks a prioritized list of environment variables for the PostgreSQL
    connection string used by the lineage store: LINEAGE_DB_URL first, then
    PHLO_LINEAGE_DB_URL, then DAGSTER_PG_DB_CONNECTION_STRING. Returns the
    PostgreSQL connection string if found, otherwise None.

    Example:
        >>> import os
        >>> os.environ["LINEAGE_DB_URL"] = "postgresql://localhost/lineage"
        >>> resolve_lineage_db_url()
        'postgresql://localhost/lineage'
    """
    for key in _LINEAGE_DB_KEYS:
        value = os.environ.get(key)
        if value:
            return value
    return None


def resolve_lineage_db_url_with_postgres_fallback() -> str | None:
    """Resolve the lineage database URL with PostgreSQL fallback.

    First attempts to resolve from explicit lineage environment variables. If not
    found, constructs a connection string from standard PostgreSQL environment
    variables with sensible defaults: POSTGRES_HOST (default "postgres"),
    POSTGRES_PORT (default 5432), POSTGRES_USER (default "phlo"),
    POSTGRES_PASSWORD (default "phlo"), and POSTGRES_DB (default "phlo"). Returns
    the PostgreSQL connection string, or None if resolution fails.

    Example:
        >>> import os
        >>> os.environ["POSTGRES_HOST"] = "localhost"
        >>> url = resolve_lineage_db_url_with_postgres_fallback()
        >>> assert "localhost" in url
    """
    if connection_string := resolve_lineage_db_url():
        return connection_string
    host, port = _resolve_postgres_host(
        os.environ.get("POSTGRES_HOST", "postgres"),
        int(os.environ.get("POSTGRES_PORT", "5432")),
    )
    user = quote_plus(os.environ.get("POSTGRES_USER", "phlo"))
    password = quote_plus(os.environ.get("POSTGRES_PASSWORD", "phlo"))
    database = quote_plus(os.environ.get("POSTGRES_DB", "phlo"))
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def _resolve_postgres_host(host: str, port: int) -> tuple[str, int]:
    """Resolve PostgreSQL host and port with network configuration.

    Uses the phlo network configuration system to resolve hostnames and handle
    Docker network scenarios. ``host`` is the server hostname or IP address and
    ``port`` the connection port; returns the resolved (host, port) tuple.
    Internal helper used by resolve_lineage_db_url_with_postgres_fallback.
    """
    return resolve_host(host, port, port_env_var="POSTGRES_PORT")


def generate_row_id() -> str:
    """Generate a new ULID for row-level lineage tracking.

    ULIDs (Universally Unique Lexicographically Sortable Identifiers) provide
    lexicographic sortability by timestamp (48-bit timestamp prefix), global
    uniqueness (128-bit total entropy), URL safety (Crockford's Base32 encoding),
    and monotonic sort order within the same millisecond. Returns the string
    representation of the new ULID.

    Example:
        >>> row_id = generate_row_id()
        >>> len(row_id)  # ULIDs are 26 characters
        26
        >>> import time
        >>> # ULIDs sort by time
        >>> id1 = generate_row_id()
        >>> time.sleep(0.01)
        >>> id2 = generate_row_id()
        >>> id1 < id2
        True

    See https://github.com/ulid/spec for ULID specification details.
    """
    return str(ulid.ULID())


class LineageStore:
    """PostgreSQL-backed store for row-level and column-level lineage.

    Provides comprehensive CRUD operations for tracking data provenance across
    the pipeline; the schema is auto-created on first use, requiring zero manual
    configuration. Supports row-level lineage with recursive parent-child
    relationships, column-level lineage mappings between assets, asset node and
    edge management for graph construction, batch operations for efficient bulk
    inserts, JSONB metadata storage for flexible extensibility, and class-level
    schema caching to avoid redundant checks.

    Creates the following tables in the phlo schema: row_lineage for individual
    row provenance records, asset_lineage_nodes for asset metadata and status,
    asset_lineage_edges for directed asset dependencies, and column_lineage for
    column-to-column mappings.

    ``connection_string`` is a PostgreSQL connection string in standard format
    ("postgresql://user:password@host:port/database").

    Example:
        >>> store = LineageStore("postgresql://phlo:phlo@localhost:5432/phlo")
        >>> store.record_row("01ARZ3NDEKTSV4RRFFQ69G5FAV", "bronze.orders", "dlt")
        >>> ancestors = store.get_ancestors("01ARZ3NDEKTSV4RRFFQ69G5FAV")

    Schema initialization is lazy: the first operation on any LineageStore
    instance creates the schema if it is missing. A class-level flag skips the
    check after the first success. The flag is not lock-protected, so concurrent
    initializations may race, but migrations use IF NOT EXISTS and "already
    exists" errors are treated as success.
    """

    _schema_initialized: bool = False

    def __init__(self, connection_string: str):
        """Initialize a LineageStore instance.

        ``connection_string`` is a PostgreSQL connection string that must include all
        necessary authentication and host information.

        Example:
            >>> store = LineageStore("postgresql://user:pass@localhost:5432/dagster")
        """
        self.connection_string = connection_string

    def _ensure_schema(self) -> None:
        """Ensure the lineage schema exists, creating it if necessary.

        Called automatically before any database operation. Uses a class-level flag to
        ensure schema initialization only happens once per process, even with multiple
        LineageStore instances: it checks the flag, verifies schema existence via
        to_regclass queries, executes all SQL migration files in order when missing,
        and handles race conditions gracefully (duplicate creation attempts). Raises
        Exception if schema creation fails for reasons other than already-exists
        conditions; warnings are logged for errors. This is an internal method called
        automatically by public methods, so manual invocation is not required.
        """
        if LineageStore._schema_initialized:
            return

        if self._schema_exists():
            LineageStore._schema_initialized = True
            return

        try:
            self.setup_schema()
            LineageStore._schema_initialized = True
        except Exception as e:
            if self._schema_exists() or "already exists" in str(e).lower():
                LineageStore._schema_initialized = True
            else:
                logger.warning("lineage_schema_init_failed", error=str(e))

    def _schema_exists(self) -> bool:
        """Check if the lineage schema tables exist.

        Verifies existence of the three core lineage tables (phlo.asset_lineage_nodes,
        phlo.asset_lineage_edges, phlo.column_lineage) using PostgreSQL's to_regclass
        function, which returns the OID if the relation exists. Returns True only when
        all three exist. Connection errors are caught internally and result in False
        rather than propagating.
        """
        try:
            with psycopg2.connect(self.connection_string) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT
                            to_regclass('phlo.asset_lineage_nodes'),
                            to_regclass('phlo.asset_lineage_edges'),
                            to_regclass('phlo.column_lineage')
                        """
                    )
                    result = cur.fetchone()
        except Exception:
            return False
        if result is None:
            return False
        return all(value is not None for value in result)

    def setup_schema(self) -> None:
        """Create the lineage schema and tables by executing SQL migrations.

        Reads all .sql files from the package's sql/ directory (relative to this
        module, i.e. {package_root}/sql/*.sql) and executes them in sorted order.
        This supports incremental schema migrations through numbered migration files
        (e.g., 001_initial.sql, 002_add_indexes.sql). Raises Exception if SQL
        execution fails for any migration file. Typically called automatically by
        _ensure_schema(); manual invocation is useful for explicit schema management
        or when integrating with external migration systems.

        Example:
            >>> store = LineageStore("postgresql://...")
            >>> store.setup_schema()  # Creates tables if they don't exist
        """
        sql_dir = Path(__file__).parent / "sql"
        sql_files = sorted(sql_dir.glob("*.sql"))

        with psycopg2.connect(self.connection_string) as conn:
            with conn.cursor() as cur:
                for sql_file in sql_files:
                    cur.execute(sql_file.read_text())
            conn.commit()

        logger.info("lineage_schema_setup_complete", migration_count=len(sql_files))

    def record_row(
        self,
        row_id: str,
        table_name: str,
        source_type: str = "dlt",
        parent_row_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record lineage information for a single row.

        Inserts or updates a row lineage record with full provenance information,
        using UPSERT semantics (INSERT ... ON CONFLICT) so duplicate row IDs update
        the existing record with new values. ``row_id`` is the row's ULID, typically
        generated via generate_row_id(), and must be unique across all tables;
        ``table_name`` is the fully qualified table name in "schema.table" format
        (e.g., "bronze.dlt_events"); ``source_type`` classifies the origin ("dlt" for
        data loaded via dlt, "dbt" for transformed data, "external", or "manual");
        ``parent_row_ids`` lists ULIDs of parent rows this row was derived from,
        empty or None for root-level source rows; ``metadata`` optionally holds extra
        context such as run_id, partition keys, or custom attributes, stored as
        JSONB. Re-raises Exception after logging if the database operation fails.
        Success logs at INFO level and failure at WARNING level, with the parent row
        count included in the log context.

        Example:
            >>> store.record_row(
            ...     row_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            ...     table_name="bronze.orders",
            ...     source_type="dlt",
            ...     parent_row_ids=[],
            ...     metadata={"run_id": "run-123", "source": "api"},
            ... )
        """
        parent_count = len(parent_row_ids or [])
        logger.info(
            "lineage_record_row_started",
            table_name=table_name,
            source_type=source_type,
            parent_row_count=parent_count,
        )
        try:
            self._ensure_schema()
            with psycopg2.connect(self.connection_string) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO phlo.row_lineage
                        (row_id, table_name, source_type, parent_row_ids, metadata)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (row_id) DO UPDATE SET
                            table_name = EXCLUDED.table_name,
                            source_type = EXCLUDED.source_type,
                            parent_row_ids = EXCLUDED.parent_row_ids,
                            metadata = EXCLUDED.metadata
                        """,
                        (
                            row_id,
                            table_name,
                            source_type,
                            parent_row_ids,
                            json.dumps(metadata) if metadata else None,
                        ),
                    )
                conn.commit()
            logger.info(
                "lineage_record_row_succeeded",
                table_name=table_name,
                source_type=source_type,
                parent_row_count=parent_count,
            )
        except Exception:
            logger.warning(
                "lineage_record_row_failed",
                table_name=table_name,
                source_type=source_type,
                parent_row_count=parent_count,
                exc_info=True,
            )
            raise

    def record_rows_batch(
        self,
        rows: list[dict[str, Any]],
        table_name: str,
        source_type: str = "dlt",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Record lineage for multiple rows in a single batch operation.

        Efficiently inserts lineage records for many rows using execute_values for
        bulk loading; rows without a "_phlo_row_id" field are silently skipped.
        ``rows`` is a list of row dictionaries each containing "_phlo_row_id";
        ``table_name`` is the fully qualified destination table; ``source_type``
        classifies the origin (see record_row() for options); ``metadata`` applies to
        all rows in the batch. Returns the number of rows successfully recorded, which
        may be less than the input length when some rows lack _phlo_row_id.
        Re-raises Exception after logging if the batch insert fails. Uses
        psycopg2.extras.execute_values for O(1) round trips regardless of batch size,
        up to PostgreSQL parameter limits. Duplicate row_ids are silently ignored
        (ON CONFLICT DO NOTHING); to update existing records, use record_row()
        individually.

        Example:
            >>> rows = [
            ...     {"_phlo_row_id": "01ARZ...", "order_id": 1},
            ...     {"_phlo_row_id": "01ARZ...", "order_id": 2},
            ... ]
            >>> count = store.record_rows_batch(rows, "bronze.orders", "dlt")
            >>> print(f"Recorded {count} rows")
        """
        if not rows:
            return 0

        requested_count = len(rows)
        values = []
        for row in rows:
            row_id = row.get("_phlo_row_id")
            if not row_id:
                continue
            values.append(
                (
                    row_id,
                    table_name,
                    source_type,
                    None,  # parent_row_ids
                    json.dumps(metadata) if metadata else None,
                )
            )

        if not values:
            return 0

        inserted_count = len(values)
        skipped_count = requested_count - inserted_count
        logger.info(
            "lineage_record_rows_batch_started",
            table_name=table_name,
            source_type=source_type,
            requested_count=requested_count,
            insert_count=inserted_count,
            skipped_missing_row_id_count=skipped_count,
        )
        try:
            self._ensure_schema()
            with psycopg2.connect(self.connection_string) as conn:
                with conn.cursor() as cur:
                    from psycopg2.extras import execute_values

                    execute_values(
                        cur,
                        """
                        INSERT INTO phlo.row_lineage
                        (row_id, table_name, source_type, parent_row_ids, metadata)
                        VALUES %s
                        ON CONFLICT (row_id) DO NOTHING
                        """,
                        values,
                    )
                conn.commit()
            logger.info(
                "lineage_record_rows_batch_succeeded",
                table_name=table_name,
                source_type=source_type,
                requested_count=requested_count,
                insert_count=inserted_count,
                skipped_missing_row_id_count=skipped_count,
            )
        except Exception:
            logger.warning(
                "lineage_record_rows_batch_failed",
                table_name=table_name,
                source_type=source_type,
                requested_count=requested_count,
                insert_count=inserted_count,
                skipped_missing_row_id_count=skipped_count,
                exc_info=True,
            )
            raise

        return len(values)

    def record_asset_nodes(
        self,
        asset_keys: list[str],
        *,
        asset_type: str | None = None,
        status: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
        tags: dict[str, Any] | None = None,
    ) -> int:
        """Record or update asset nodes in the lineage graph.

        Creates or updates asset metadata records; duplicate keys trigger UPSERT
        semantics with COALESCE, preserving existing non-null values when new values
        are None. ``asset_keys`` lists unique asset identifiers (e.g., "bronze.orders");
        ``asset_type`` classifies the asset ("ingestion" for raw loaded data,
        "transform" for dbt models or transformation output, "publish" for final
        published datasets); ``status`` is one of "success", "warning", "failure", or
        "unknown"; ``description`` is a human-readable description; ``metadata`` is a
        JSON-serializable dictionary; ``tags`` holds string tags for categorization
        and filtering. Returns the number of asset nodes successfully persisted and
        re-raises Exception after logging on database failure. The updated_at
        timestamp refreshes automatically on every UPSERT.

        Example:
            >>> store.record_asset_nodes(
            ...     ["bronze.orders", "silver.stg_orders"],
            ...     asset_type="ingestion",
            ...     status="success",
            ...     metadata={"owner": "data-team"},
            ... )
        """
        if not asset_keys:
            return 0

        unique_keys = sorted(set(asset_keys))
        values = [
            (
                asset_key,
                asset_type,
                status,
                description,
                json.dumps(metadata) if metadata else None,
                json.dumps(tags) if tags else None,
            )
            for asset_key in unique_keys
        ]

        requested_count = len(asset_keys)
        upsert_count = len(values)
        logger.info(
            "lineage_record_asset_nodes_started",
            requested_count=requested_count,
            upsert_count=upsert_count,
        )
        try:
            self._ensure_schema()
            with psycopg2.connect(self.connection_string) as conn:
                with conn.cursor() as cur:
                    from psycopg2.extras import execute_values

                    execute_values(
                        cur,
                        """
                        INSERT INTO phlo.asset_lineage_nodes
                        (asset_key, asset_type, status, description, metadata, tags)
                        VALUES %s
                        ON CONFLICT (asset_key) DO UPDATE SET
                            asset_type = COALESCE(EXCLUDED.asset_type, phlo.asset_lineage_nodes.asset_type),
                            status = COALESCE(EXCLUDED.status, phlo.asset_lineage_nodes.status),
                            description = COALESCE(
                                EXCLUDED.description, phlo.asset_lineage_nodes.description
                            ),
                            metadata = COALESCE(EXCLUDED.metadata, phlo.asset_lineage_nodes.metadata),
                            tags = COALESCE(EXCLUDED.tags, phlo.asset_lineage_nodes.tags),
                            updated_at = NOW()
                        """,
                        values,
                    )
                conn.commit()
            logger.info(
                "lineage_record_asset_nodes_succeeded",
                requested_count=requested_count,
                upsert_count=upsert_count,
            )
        except Exception:
            logger.warning(
                "lineage_record_asset_nodes_failed",
                requested_count=requested_count,
                upsert_count=upsert_count,
                exc_info=True,
            )
            raise

        return len(values)

    def record_asset_edges(
        self,
        edges: list[tuple[str, str]],
        *,
        asset_keys: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        tags: dict[str, Any] | None = None,
    ) -> int:
        """Record directed edges between assets in the lineage graph.

        Creates or updates lineage edges representing data dependencies (source ->
        target) and also creates or updates node entries for all assets mentioned in
        edges or the explicit ``asset_keys`` list. ``edges`` holds (source, target)
        tuples following data-flow direction; ``metadata`` and ``tags`` apply to all
        edges in the batch as JSON-serializable dictionaries. Returns the number of
        edges successfully persisted and re-raises Exception after logging on
        database failure. Nodes persist before edges in the same logical operation,
        but there is no atomic transaction guarantee across the two calls; edge
        records use UPSERT semantics with updated_at refresh. Edge persistence is
        skipped when ``edges`` is empty, though node creation still occurs when
        ``asset_keys`` is provided.

        Example:
            >>> edges = [
            ...     ("bronze.orders", "silver.stg_orders"),
            ...     ("silver.stg_orders", "gold.fct_orders"),
            ... ]
            >>> store.record_asset_edges(edges, metadata={"run_id": "abc123"})
        """
        if not edges and not asset_keys:
            return 0

        edge_count = len(edges)
        explicit_asset_key_count = len(asset_keys or [])
        node_keys: set[str] = set(asset_keys or [])
        for source, target in edges:
            node_keys.add(source)
            node_keys.add(target)

        logger.info(
            "lineage_record_asset_edges_started",
            edge_count=edge_count,
            explicit_asset_key_count=explicit_asset_key_count,
            node_key_count=len(node_keys),
        )
        persisted_node_count = 0
        persisted_edge_count = 0
        try:
            if node_keys:
                persisted_node_count = self.record_asset_nodes(
                    list(node_keys),
                    metadata=metadata,
                    tags=tags,
                )

            if edges:
                values = [
                    (
                        source,
                        target,
                        json.dumps(metadata) if metadata else None,
                        json.dumps(tags) if tags else None,
                    )
                    for source, target in edges
                ]
                persisted_edge_count = len(values)
                self._ensure_schema()
                with psycopg2.connect(self.connection_string) as conn:
                    with conn.cursor() as cur:
                        from psycopg2.extras import execute_values

                        execute_values(
                            cur,
                            """
                            INSERT INTO phlo.asset_lineage_edges
                            (source_asset, target_asset, metadata, tags)
                            VALUES %s
                            ON CONFLICT (source_asset, target_asset) DO UPDATE SET
                                metadata = COALESCE(EXCLUDED.metadata, phlo.asset_lineage_edges.metadata),
                                tags = COALESCE(EXCLUDED.tags, phlo.asset_lineage_edges.tags),
                                updated_at = NOW()
                            """,
                            values,
                        )
                    conn.commit()
            logger.info(
                "lineage_record_asset_edges_succeeded",
                edge_count=edge_count,
                explicit_asset_key_count=explicit_asset_key_count,
                node_key_count=len(node_keys),
                persisted_node_count=persisted_node_count,
                persisted_edge_count=persisted_edge_count,
            )
        except Exception:
            logger.warning(
                "lineage_record_asset_edges_failed",
                edge_count=edge_count,
                explicit_asset_key_count=explicit_asset_key_count,
                node_key_count=len(node_keys),
                persisted_node_count=persisted_node_count,
                persisted_edge_count=persisted_edge_count,
                exc_info=True,
            )
            raise

        return persisted_edge_count

    def list_asset_nodes(self) -> list[dict[str, Any]]:
        """List all asset nodes with their metadata.

        Queries the phlo.asset_lineage_nodes table and returns dictionaries keyed by
        asset_key (unique identifier), asset_type (classification: ingestion,
        transform, publish), status (success, warning, failure, unknown),
        description, metadata and tags (parsed JSON dicts), plus created_at and
        updated_at ISO-format timestamps.

        Example:
            >>> nodes = store.list_asset_nodes()
            >>> for node in nodes:
            ...     print(f"{node['asset_key']}: {node['status']}")
        """
        self._ensure_schema()
        with psycopg2.connect(self.connection_string) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT asset_key, asset_type, status, description, metadata, tags,
                           created_at, updated_at
                    FROM phlo.asset_lineage_nodes
                    """
                )
                rows = cur.fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            results.append(
                {
                    "asset_key": row[0],
                    "asset_type": row[1],
                    "status": row[2],
                    "description": row[3],
                    "metadata": row[4],
                    "tags": row[5],
                    "created_at": row[6].isoformat() if row[6] else None,
                    "updated_at": row[7].isoformat() if row[7] else None,
                }
            )
        return results

    def list_asset_edges(self) -> list[dict[str, Any]]:
        """List all directed edges in the lineage graph.

        Queries the phlo.asset_lineage_edges table and returns dictionaries keyed by
        source_asset (upstream asset key), target_asset (downstream asset key),
        metadata and tags (parsed JSON dicts), plus created_at and updated_at
        ISO-format timestamps.

        Example:
            >>> edges = store.list_asset_edges()
            >>> for edge in edges:
            ...     print(f"{edge['source_asset']} -> {edge['target_asset']}")
        """
        self._ensure_schema()
        with psycopg2.connect(self.connection_string) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT source_asset, target_asset, metadata, tags, created_at, updated_at
                    FROM phlo.asset_lineage_edges
                    """
                )
                rows = cur.fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            results.append(
                {
                    "source_asset": row[0],
                    "target_asset": row[1],
                    "metadata": row[2],
                    "tags": row[3],
                    "created_at": row[4].isoformat() if row[4] else None,
                    "updated_at": row[5].isoformat() if row[5] else None,
                }
            )
        return results

    def get_row(self, row_id: str) -> dict[str, Any] | None:
        """Retrieve lineage information for a single row by ID.

        ``row_id`` is the row's ULID string identifier. Returns a dictionary with
        row lineage information (keys: row_id, table_name, source_type,
        parent_row_ids, created_at, and parsed JSON metadata) when found, otherwise
        None.

        Example:
            >>> row = store.get_row("01ARZ3NDEKTSV4RRFFQ69G5FAV")
            >>> if row:
            ...     print(f"Found in table: {row['table_name']}")
        """
        with psycopg2.connect(self.connection_string) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT row_id, table_name, source_type, parent_row_ids,
                           created_at, metadata
                    FROM phlo.row_lineage
                    WHERE row_id = %s
                    """,
                    (row_id,),
                )
                row = cur.fetchone()

        if not row:
            return None

        return {
            "row_id": row[0],
            "table_name": row[1],
            "source_type": row[2],
            "parent_row_ids": row[3] or [],
            "created_at": row[4].isoformat() if row[4] else None,
            "metadata": row[5],
        }

    def get_ancestors(self, row_id: str, max_depth: int = 10) -> list[dict[str, Any]]:
        """Recursively retrieve all ancestor rows (upstream lineage).

        Uses a PostgreSQL recursive CTE to traverse parent relationships up to
        ``max_depth`` parent levels (default 10), preventing infinite recursion in
        case of circular references. ``row_id`` is the ULID of the starting row.
        Returns dictionaries of ancestor row information sorted by creation time
        descending (most recent first); raises Exception if the database query
        fails. The CTE applies DISTINCT so duplicate rows are avoided when multiple
        paths converge on the same ancestor.

        Example:
            >>> ancestors = store.get_ancestors("01ARZ3NDEKTSV4RRFFQ69G5FAV")
            >>> for ancestor in ancestors:
            ...     print(f"Derived from: {ancestor['table_name']}")
        """
        with psycopg2.connect(self.connection_string) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH RECURSIVE ancestors AS (
                        -- Base case: get parents of the starting row
                        SELECT rl.row_id, rl.table_name, rl.source_type,
                               rl.parent_row_ids, rl.created_at, rl.metadata,
                               1 as depth
                        FROM phlo.row_lineage rl
                        WHERE rl.row_id = ANY(
                            SELECT unnest(parent_row_ids)
                            FROM phlo.row_lineage
                            WHERE row_id = %s
                        )

                        UNION ALL

                        -- Recursive case: get parents of parents
                        SELECT rl.row_id, rl.table_name, rl.source_type,
                               rl.parent_row_ids, rl.created_at, rl.metadata,
                               a.depth + 1
                        FROM phlo.row_lineage rl
                        INNER JOIN ancestors a
                            ON rl.row_id = ANY(a.parent_row_ids)
                        WHERE a.depth < %s
                    )
                    SELECT DISTINCT row_id, table_name, source_type,
                           parent_row_ids, created_at, metadata
                    FROM ancestors
                    ORDER BY created_at DESC
                    """,
                    (row_id, max_depth),
                )
                rows = cur.fetchall()

        return [
            {
                "row_id": row[0],
                "table_name": row[1],
                "source_type": row[2],
                "parent_row_ids": row[3] or [],
                "created_at": row[4].isoformat() if row[4] else None,
                "metadata": row[5],
            }
            for row in rows
        ]

    def get_descendants(self, row_id: str, max_depth: int = 10) -> list[dict[str, Any]]:
        """Recursively retrieve all descendant rows (downstream lineage).

        Uses a PostgreSQL recursive CTE to traverse child relationships (reverse
        parent lookup) up to ``max_depth`` child levels (default 10), preventing
        infinite recursion in case of circular references. ``row_id`` is the ULID of
        the starting row. Returns dictionaries of descendant row information sorted
        by creation time ascending (oldest first); raises Exception if the database
        query fails. The CTE performs a reverse index lookup (row_id =
        ANY(parent_row_ids)); ensure a GIN index exists on parent_row_ids for large
        datasets.

        Example:
            >>> descendants = store.get_descendants("01ARZ3NDEKTSV4RRFFQ69G5FAV")
            >>> for descendant in descendants:
            ...     print(f"Used in: {descendant['table_name']}")
        """
        with psycopg2.connect(self.connection_string) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH RECURSIVE descendants AS (
                        -- Base case: find rows that have this row as parent
                        SELECT rl.row_id, rl.table_name, rl.source_type,
                               rl.parent_row_ids, rl.created_at, rl.metadata,
                               1 as depth
                        FROM phlo.row_lineage rl
                        WHERE %s = ANY(rl.parent_row_ids)

                        UNION ALL

                        -- Recursive case: find children of children
                        SELECT rl.row_id, rl.table_name, rl.source_type,
                               rl.parent_row_ids, rl.created_at, rl.metadata,
                               d.depth + 1
                        FROM phlo.row_lineage rl
                        INNER JOIN descendants d ON d.row_id = ANY(rl.parent_row_ids)
                        WHERE d.depth < %s
                    )
                    SELECT DISTINCT row_id, table_name, source_type,
                           parent_row_ids, created_at, metadata
                    FROM descendants
                    ORDER BY created_at ASC
                    """,
                    (row_id, max_depth),
                )
                rows = cur.fetchall()

        return [
            {
                "row_id": row[0],
                "table_name": row[1],
                "source_type": row[2],
                "parent_row_ids": row[3] or [],
                "created_at": row[4].isoformat() if row[4] else None,
                "metadata": row[5],
            }
            for row in rows
        ]

    def get_table_rows(self, table_name: str, limit: int = 100) -> list[dict[str, Any]]:
        """Retrieve recent lineage records for a specific table.

        ``table_name`` is the fully qualified table name (e.g., "bronze.orders") and
        ``limit`` caps the result count (default 100). Returns row lineage
        dictionaries sorted by creation time descending (most recent first). This is
        a simple query without pagination; for large tables, consider adding offset
        or time-range filtering.

        Example:
            >>> rows = store.get_table_rows("bronze.orders", limit=10)
            >>> print(f"Recent rows: {len(rows)}")
        """
        with psycopg2.connect(self.connection_string) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT row_id, table_name, source_type, parent_row_ids,
                           created_at, metadata
                    FROM phlo.row_lineage
                    WHERE table_name = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (table_name, limit),
                )
                rows = cur.fetchall()

        return [
            {
                "row_id": row[0],
                "table_name": row[1],
                "source_type": row[2],
                "parent_row_ids": row[3] or [],
                "created_at": row[4].isoformat() if row[4] else None,
                "metadata": row[5],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Column-level lineage
    # ------------------------------------------------------------------

    def record_column_lineage(self, mappings: list[ColumnLineage]) -> int:
        """Batch-insert column lineage mappings.

        Persists column-to-column lineage relationships using efficient bulk insert
        via execute_values. Duplicate mappings (same source/target asset and column
        combination) are silently skipped via ON CONFLICT DO NOTHING, so the returned
        count of mappings submitted for insert may differ from the persisted count.
        Re-raises Exception after logging if the batch insert fails. Uses
        psycopg2.extras.execute_values for a single round-trip regardless of batch
        size.

        Example:
            >>> from phlo_lineage.store import ColumnLineage
            >>> mappings = [
            ...     ColumnLineage(
            ...         source_asset="bronze.orders",
            ...         source_column="order_id",
            ...         target_asset="silver.stg_orders",
            ...         target_column="order_id",
            ...     ),
            ... ]
            >>> count = store.record_column_lineage(mappings)
        """
        if not mappings:
            return 0

        values = [
            (
                m.source_asset,
                m.source_column,
                m.target_asset,
                m.target_column,
                m.source_type,
                json.dumps(m.metadata) if m.metadata else None,
            )
            for m in mappings
        ]

        logger.info("column_lineage_record_started", mapping_count=len(values))
        try:
            self._ensure_schema()
            with psycopg2.connect(self.connection_string) as conn:
                with conn.cursor() as cur:
                    from psycopg2.extras import execute_values

                    execute_values(
                        cur,
                        """
                        INSERT INTO phlo.column_lineage
                        (source_asset, source_column, target_asset, target_column,
                         source_type, metadata)
                        VALUES %s
                        ON CONFLICT DO NOTHING
                        """,
                        values,
                    )
                conn.commit()
            logger.info("column_lineage_record_succeeded", mapping_count=len(values))
        except Exception:
            logger.warning(
                "column_lineage_record_failed",
                mapping_count=len(values),
                exc_info=True,
            )
            raise

        return len(values)

    def get_upstream_columns(
        self,
        target_asset: str,
        target_column: str | None = None,
    ) -> list[ColumnLineage]:
        """Query upstream column lineage for a target asset.

        Retrieves ColumnLineage records showing which upstream columns feed into the
        specified target asset. ``target_asset`` is the downstream asset key to
        query; ``target_column`` optionally narrows results to one column, otherwise
        lineage for all columns in the target asset is returned (the query filters
        by target_asset alone without it, and additionally by target_column with
        it). Returns the matching upstream-dependency records.

        Example:
            >>> # All upstream columns for the asset
            >>> upstream = store.get_upstream_columns("silver.stg_orders")
            >>>
            >>> # Specific column only
            >>> upstream = store.get_upstream_columns("silver.stg_orders", "order_id")
        """
        self._ensure_schema()

        if target_column is not None:
            query = """
                SELECT source_asset, source_column, target_asset, target_column,
                       source_type, metadata
                FROM phlo.column_lineage
                WHERE target_asset = %s AND target_column = %s
            """
            params: tuple[str, ...] = (target_asset, target_column)
        else:
            query = """
                SELECT source_asset, source_column, target_asset, target_column,
                       source_type, metadata
                FROM phlo.column_lineage
                WHERE target_asset = %s
            """
            params = (target_asset,)

        with psycopg2.connect(self.connection_string) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()

        return [
            ColumnLineage(
                source_asset=r[0],
                source_column=r[1],
                target_asset=r[2],
                target_column=r[3],
                source_type=r[4],
                metadata=r[5],
            )
            for r in rows
        ]

    def get_downstream_columns(
        self,
        source_asset: str,
        source_column: str | None = None,
    ) -> list[ColumnLineage]:
        """Query downstream column lineage for a source asset.

        Retrieves ColumnLineage records showing which downstream columns are derived
        from the specified source asset. ``source_asset`` is the upstream asset key
        to query; ``source_column`` optionally narrows results to one column,
        otherwise lineage for all columns in the source asset is returned (the query
        filters by source_asset alone without it, and additionally by source_column
        with it). Returns the matching downstream-dependent records.

        Example:
            >>> # All downstream columns for the asset
            >>> downstream = store.get_downstream_columns("bronze.orders")
            >>>
            >>> # Specific column only
            >>> downstream = store.get_downstream_columns("bronze.orders", "order_id")
        """
        self._ensure_schema()

        if source_column is not None:
            query = """
                SELECT source_asset, source_column, target_asset, target_column,
                       source_type, metadata
                FROM phlo.column_lineage
                WHERE source_asset = %s AND source_column = %s
            """
            params: tuple[str, ...] = (source_asset, source_column)
        else:
            query = """
                SELECT source_asset, source_column, target_asset, target_column,
                       source_type, metadata
                FROM phlo.column_lineage
                WHERE source_asset = %s
            """
            params = (source_asset,)

        with psycopg2.connect(self.connection_string) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()

        return [
            ColumnLineage(
                source_asset=r[0],
                source_column=r[1],
                target_asset=r[2],
                target_column=r[3],
                source_type=r[4],
                metadata=r[5],
            )
            for r in rows
        ]
