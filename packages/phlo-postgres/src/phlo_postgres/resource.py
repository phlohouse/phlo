"""PostgreSQL connection resource with pooling for publishing and operational writes.

This module provides a lightweight, context-managed PostgreSQL resource that handles
connection pooling, transaction management, and health checks. It is designed for
operational writes and data publishing workflows.

Example:
    >>> from phlo_postgres import PostgresResource
    >>>
    >>> # Context manager usage (recommended)
    >>> with PostgresResource() as db:
    ...     db.execute("INSERT INTO logs (msg) VALUES (%s)", ("hello",))
    ...     rows = db.query("SELECT * FROM logs")
    ...
    >>> # Manual lifecycle management
    >>> db = PostgresResource(host="localhost", port=5432)
    >>> db.connect()
    >>> if db.is_healthy():
    ...     result = db.query_one("SELECT COUNT(*) FROM users")
    >>> db.close()


Re-exported as PostgresResource from the phlo_postgres package root for
publishing and operational writes across the platform.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from psycopg2 import pool, sql

from phlo.logging import get_logger
from phlo_postgres.settings import get_settings

logger = get_logger(__name__)


@dataclass
class PostgresResource:
    """Lightweight PostgreSQL connection resource with connection pooling.

    This class manages PostgreSQL connections using a connection pool for efficiency.
    It supports both context manager usage (recommended) and manual lifecycle management.
    Transactions are automatically handled when using the transactional_cursor context manager.

    Connection parameters (host, port, user, password, database) fall back to the
    settings defaults when left as None. The pool is created on first use and the
    active connection is acquired from it on demand.

    Example:
        >>> # Basic usage with defaults from settings
        >>> with PostgresResource() as db:
        ...     db.ensure_schema("analytics")
        ...     db.execute("CREATE TABLE IF NOT EXISTS analytics.events (id SERIAL)")
        >>>
        >>> # Custom connection parameters
        >>> with PostgresResource(host="prod.db.internal", database="analytics") as db:
        ...     rows = db.query("SELECT * FROM events WHERE date > %s", ("2024-01-01",))

    """

    host: str | None = None
    port: int | None = None
    user: str | None = None
    password: str | None = None
    database: str | None = None
    min_connections: int = 1
    max_connections: int = 5
    _pool: pool.SimpleConnectionPool | None = field(default=None, init=False, repr=False)
    _connection: Any | None = field(default=None, init=False, repr=False)

    def __enter__(self) -> "PostgresResource":
        """Initialize the resource for context-managed usage.

        This method ensures a connection is available when entering the context.
        The connection is automatically returned to the pool when exiting.

        The initialized resource instance is returned ready for queries.

        Example:
            >>> with PostgresResource() as db:
            ...     # Connection is now active
            ...     db.execute("SELECT 1")

        """
        self._ensure_connection()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """Clean up the resource on context exit.

        Performs rollback if an exception occurred, then returns the connection to
        the pool and closes the pool. Rollback failures are logged, not raised, so
        the original exception propagates.
        """
        if exc_type is not None:
            try:
                self.rollback()
            except Exception:  # noqa: BLE001 - best effort rollback on context exit
                logger.warning("postgres_resource_rollback_failed", exc_info=True)
        try:
            self.close()
        finally:
            self.close_pool()

    def __del__(self) -> None:
        """Best-effort cleanup during object destruction.

        Attempts to close the connection and pool if the object is garbage collected
        without proper cleanup. Failures are silently logged to prevent destruction
        errors from interfering with program termination.
        """
        try:
            self.close()
        except Exception:  # noqa: BLE001 - destructor must never raise
            logger.debug("postgres_resource_close_on_del_failed", exc_info=True)
        try:
            self.close_pool()
        except Exception:  # noqa: BLE001 - destructor must never raise
            logger.debug("postgres_resource_pool_close_on_del_failed", exc_info=True)

    def _ensure_pool(self) -> pool.SimpleConnectionPool:
        """Create or return the connection pool.

        Lazy-initializes the connection pool on first access using configured or
        default settings. Connection parameters are resolved in order:
        explicit attribute > settings default > built-in default.

        Raises psycopg2.Error if pool creation fails (e.g., bad credentials,
        host unreachable).

        Example:
            >>> db = PostgresResource()
            >>> pool = db._ensure_pool()  # Creates pool on first call
            >>> same_pool = db._ensure_pool()  # Returns existing pool

        """
        if self._pool is None or self._pool.closed:
            settings = get_settings()
            host = self.host or settings.postgres_host
            port = self.port or settings.postgres_port
            database = self.database or settings.postgres_db
            start = perf_counter()
            logger.info(
                "postgres_pool_creation_started",
                host=host,
                port=port,
                database=database,
                min_connections=self.min_connections,
                max_connections=self.max_connections,
            )
            try:
                self._pool = pool.SimpleConnectionPool(
                    minconn=self.min_connections,
                    maxconn=self.max_connections,
                    host=host,
                    port=port,
                    user=self.user or settings.postgres_user,
                    password=self.password or settings.postgres_password,
                    dbname=database,
                )
            except Exception:
                logger.error(
                    "postgres_pool_creation_failed",
                    host=host,
                    port=port,
                    database=database,
                    elapsed_ms=round((perf_counter() - start) * 1000, 2),
                    exc_info=True,
                )
                raise
            logger.info(
                "postgres_pool_creation_completed",
                host=host,
                port=port,
                database=database,
                elapsed_ms=round((perf_counter() - start) * 1000, 2),
            )
        return self._pool

    def _ensure_connection(self):
        """Acquire a connection from the pool.

        Returns a healthy connection from the pool, creating the pool if needed.
        Stale connections are detected and replaced automatically.

        Raises psycopg2.Error if connection acquisition fails.

        Example:
            >>> db = PostgresResource()
            >>> conn = db._ensure_connection()
            >>> with conn.cursor() as cur:
            ...     cur.execute("SELECT 1")

        """
        if self._connection is None or getattr(self._connection, "closed", 1):
            connection_pool = self._ensure_pool()
            # Return the stale connection slot before acquiring a new one
            if self._connection is not None:
                try:
                    connection_pool.putconn(self._connection, close=True)
                except Exception:  # noqa: BLE001 - best effort return
                    logger.debug("postgres_resource_stale_connection_return_failed", exc_info=True)
                self._connection = None
            start = perf_counter()
            logger.info("postgres_resource_connection_started")
            try:
                self._connection = connection_pool.getconn()
            except Exception:
                logger.error(
                    "postgres_resource_connection_failed",
                    elapsed_ms=round((perf_counter() - start) * 1000, 2),
                    exc_info=True,
                )
                raise
            logger.info(
                "postgres_resource_connection_completed",
                elapsed_ms=round((perf_counter() - start) * 1000, 2),
            )
        return self._connection

    @contextmanager
    def cursor(self):
        """Provide a cursor for manual transaction control.

        Yields a psycopg2 cursor. The caller is responsible for committing or
        rolling back transactions. Useful when you need fine-grained control
        over transaction boundaries.

        Example:
            >>> with PostgresResource() as db:
            ...     with db.cursor() as cur:
            ...         cur.execute("BEGIN")
            ...         cur.execute("INSERT INTO logs VALUES (%s)", ("entry",))
            ...         # Manual commit/rollback based on business logic
            ...         cur.execute("COMMIT")

        """
        connection = self._ensure_connection()
        cursor = connection.cursor()
        try:
            yield cursor
        finally:
            cursor.close()

    @contextmanager
    def transactional_cursor(self):
        """Provide a cursor with automatic commit/rollback handling.

        Yields a cursor and automatically commits on success or rolls back on
        exception. This is the recommended way to perform write operations.

        Any exception is re-raised after the rollback.

        Example:
            >>> with PostgresResource() as db:
            ...     try:
            ...         with db.transactional_cursor() as cur:
            ...             cur.execute("INSERT INTO events (msg) VALUES (%s)", ("click",))
            ...             cur.execute("UPDATE counters SET count = count + 1")
            ...             # Both operations committed atomically on success
            ...     except psycopg2.Error:
            ...         # Both operations rolled back on failure
            ...         pass

        """
        connection = self._ensure_connection()
        cursor = connection.cursor()
        start = perf_counter()
        logger.info("postgres_transaction_started")
        try:
            yield cursor
        except Exception:
            logger.warning(
                "postgres_transaction_rollback",
                elapsed_ms=round((perf_counter() - start) * 1000, 2),
                exc_info=True,
            )
            connection.rollback()
            raise
        else:
            connection.commit()
            logger.info(
                "postgres_transaction_committed",
                elapsed_ms=round((perf_counter() - start) * 1000, 2),
            )
        finally:
            cursor.close()

    def commit(self) -> None:
        """Commit the current transaction explicitly.

        Commits any pending changes in the current connection. Use this when
        managing transactions manually with the cursor() context manager.

        Example:
            >>> with PostgresResource() as db:
            ...     with db.cursor() as cur:
            ...         cur.execute("INSERT INTO logs VALUES (%s)", ("entry",))
            ...     db.commit()  # Explicit commit

        """
        start = perf_counter()
        self._ensure_connection().commit()
        logger.info(
            "postgres_commit_completed",
            elapsed_ms=round((perf_counter() - start) * 1000, 2),
        )

    def rollback(self) -> None:
        """Roll back the current transaction explicitly.

        Reverts any pending changes in the current connection. Use this when
        managing transactions manually and an error occurs.

        Example:
            >>> with PostgresResource() as db:
            ...     with db.cursor() as cur:
            ...         try:
            ...             cur.execute("INSERT INTO logs VALUES (%s)", ("entry",))
            ...         except psycopg2.Error:
            ...             db.rollback()
            ...             raise

        """
        start = perf_counter()
        self._ensure_connection().rollback()
        logger.info(
            "postgres_rollback_completed",
            elapsed_ms=round((perf_counter() - start) * 1000, 2),
        )

    def close(self) -> None:
        """Return the current connection to the pool.

        Returns the active connection to the pool for reuse by other operations.
        Safe to call multiple times; subsequent calls are no-ops.

        Example:
            >>> db = PostgresResource()
            >>> db._ensure_connection()
            >>> # ... do work ...
            >>> db.close()  # Return connection to pool

        """
        if self._connection is not None and self._pool is not None:
            start = perf_counter()
            logger.info("postgres_resource_connection_return_started")
            try:
                self._pool.putconn(self._connection)
            except Exception:
                logger.warning("postgres_resource_connection_return_failed", exc_info=True)
                raise
            logger.info(
                "postgres_resource_connection_return_completed",
                elapsed_ms=round((perf_counter() - start) * 1000, 2),
            )
        self._connection = None

    def close_pool(self) -> None:
        """Close all connections in the pool.

        Closes all connections in the pool and releases associated resources.
        Safe to call multiple times; subsequent calls are no-ops.

        Warning:
            This terminates all pooled connections. Ensure no operations are
            in progress before calling.

        Example:
            >>> db = PostgresResource()
            >>> # ... do work ...
            >>> db.close_pool()  # Clean shutdown

        """
        if self._pool is not None and not self._pool.closed:
            start = perf_counter()
            logger.info("postgres_pool_close_started")
            try:
                self._pool.closeall()
            except Exception:
                logger.warning("postgres_pool_close_failed", exc_info=True)
                raise
            logger.info(
                "postgres_pool_close_completed",
                elapsed_ms=round((perf_counter() - start) * 1000, 2),
            )
        self._pool = None

    def is_healthy(self) -> bool:
        """Check if the database connection is alive and responsive.

        Performs a simple health check by executing "SELECT 1" and returns
        True if the query succeeds.

        Example:
            >>> with PostgresResource() as db:
            ...     if db.is_healthy():
            ...         print("Database is up")
            ...     else:
            ...         print("Database connection failed")

        """
        try:
            conn = self._ensure_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return True
        except Exception:
            logger.warning("postgres_health_check_failed", exc_info=True)
            return False

    def execute(self, sql_stmt: str, params: tuple | None = None) -> None:
        """Execute a SQL statement without returning results.

        Executes a SQL statement (INSERT, UPDATE, DELETE, DDL, etc.) and
        commits the transaction immediately. For queries that return data,
        use query() or query_one() instead.

        Raises psycopg2.Error if the SQL execution fails.

        Example:
            >>> with PostgresResource() as db:
            ...     # DDL
            ...     db.execute("CREATE TABLE users (id SERIAL PRIMARY KEY)")
            ...     # DML with parameters
            ...     db.execute("INSERT INTO users (name) VALUES (%s)", ("Alice",))

        """
        start = perf_counter()
        logger.info("postgres_execute_started")
        with self.cursor() as cur:
            cur.execute(sql_stmt, params)
        self.commit()
        logger.info(
            "postgres_execute_completed",
            elapsed_ms=round((perf_counter() - start) * 1000, 2),
        )

    def query(self, sql_stmt: str, params: tuple | None = None) -> list[tuple]:
        """Execute a SQL query and return all result rows.

        Executes a SELECT query and returns all rows as a list of tuples.
        For large result sets, consider using a cursor directly to stream results.

        Returns an empty list when the query has no results. Raises psycopg2.Error
        if the query execution fails.

        Example:
            >>> with PostgresResource() as db:
            ...     # Simple query
            ...     rows = db.query("SELECT id, name FROM users")
            ...     # Parameterized query
            ...     rows = db.query("SELECT * FROM users WHERE age > %s", (18,))
            ...     for user_id, name in rows:
            ...         print(f"{user_id}: {name}")

        """
        start = perf_counter()
        logger.info("postgres_query_started")
        with self.cursor() as cur:
            cur.execute(sql_stmt, params)
            rows = cur.fetchall()
        logger.info(
            "postgres_query_completed",
            row_count=len(rows),
            elapsed_ms=round((perf_counter() - start) * 1000, 2),
        )
        return rows

    def query_one(self, sql_stmt: str, params: tuple | None = None) -> tuple | None:
        """Execute a SQL query and return the first result row.

        Executes a SELECT query and returns only the first row, or None if
        no results. Useful for queries expected to return at most one row
        (e.g., lookups by primary key).

        Returns `None` when the query has no rows. Raises psycopg2.Error if the
        query execution fails.

        Example:
            >>> with PostgresResource() as db:
            ...     # Lookup by ID
            ...     row = db.query_one("SELECT * FROM users WHERE id = %s", (42,))
            ...     if row:
            ...         user_id, name, email = row
            ...     # Aggregate query
            ...     count_row = db.query_one("SELECT COUNT(*) FROM users")
            ...     user_count = count_row[0] if count_row else 0

        """
        start = perf_counter()
        logger.info("postgres_query_one_started")
        with self.cursor() as cur:
            cur.execute(sql_stmt, params)
            row = cur.fetchone()
        logger.info(
            "postgres_query_one_completed",
            has_result=row is not None,
            elapsed_ms=round((perf_counter() - start) * 1000, 2),
        )
        return row

    def ensure_schema(self, schema_name: str) -> None:
        """Create a database schema if it does not exist.

        Idempotent schema creation using CREATE SCHEMA IF NOT EXISTS.
        Safe to call multiple times; subsequent calls are no-ops if the
        schema already exists.

        Raises psycopg2.Error if schema creation fails (e.g., permission denied).

        Example:
            >>> with PostgresResource() as db:
            ...     # Create analytics schema
            ...     db.ensure_schema("analytics")
            ...     # Create table in the new schema
            ...     db.execute("CREATE TABLE analytics.events (id SERIAL)")

        """
        with self.transactional_cursor() as cur:
            cur.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema_name))
            )
        logger.info("postgres_schema_ensured", schema_name=schema_name)
