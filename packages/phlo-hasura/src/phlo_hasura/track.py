"""Hasura table tracking and auto-discovery.

Discovers PostgreSQL schemas and tables automatically and tracks them in
Hasura, including foreign key relationship detection and bulk table
operations. The main entry points are HasuraTableTracker, auto_track,
and auto_track_all.

Example:
    >>> from phlo_hasura.track import HasuraTableTracker, auto_track
    >>> tracker = HasuraTableTracker()
    >>> tracker.track_tables("api")
    >>> auto_track("api")

"""

from typing import Any

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from phlo.config.base import BaseConfig
from phlo.config.network import resolve_host
from phlo.logging import get_logger
from phlo_hasura.client import HasuraClient
from pydantic import Field

logger = get_logger(__name__)


class HasuraPostgresSettings(BaseConfig):
    """PostgreSQL connection settings used by Hasura table tracking.

    Pydantic model for PostgreSQL connection configuration with sensible
    defaults for Docker environments. Host and port are resolved on
    construction to handle running outside Docker containers.

    Example:
            >>> settings = HasuraPostgresSettings()
            >>> print(f"Connecting to {settings.postgres_host}:{settings.postgres_port}")
            Connecting to postgres:5432
            >>> custom = HasuraPostgresSettings(postgres_host="localhost")

    """

    postgres_host: str = Field(default="postgres", description="PostgreSQL host")
    postgres_port: int = Field(default=5432, description="PostgreSQL port")
    postgres_user: str = Field(default="phlo", description="PostgreSQL username")
    postgres_password: str = Field(default="phlo", description="PostgreSQL password")
    postgres_db: str = Field(default="phlo", description="PostgreSQL database name")

    def model_post_init(self, __context: object) -> None:
        """Resolve the PostgreSQL host and port and store them, bypassing validation."""
        host, port = resolve_host(
            self.postgres_host,
            self.postgres_port,
            port_env_var="POSTGRES_PORT",
        )
        # object.__setattr__ skips pydantic's validated assignment, which is
        # fine here: the resolved values keep the declared types.
        object.__setattr__(self, "postgres_host", host)
        object.__setattr__(self, "postgres_port", port)


class HasuraTableTracker:
    """Automatically discovers and tracks PostgreSQL tables in Hasura.

    Provides methods for schema discovery, table tracking, relationship
    creation from foreign keys, and default permission setup.

    Example:
        >>> tracker = HasuraTableTracker()
        >>> schemas = tracker.discover_user_schemas()
        >>> results = tracker.track_tables("api")
        >>> tracker.setup_relationships("api")

    """

    def __init__(
        self,
        hasura_client: HasuraClient | None = None,
        db_host: str | None = None,
        db_port: int | None = None,
        db_name: str | None = None,
        db_user: str | None = None,
        db_password: str | None = None,
    ):
        """Initialize the tracker with Hasura client and PostgreSQL credentials.

        Omitted arguments default to HasuraPostgresSettings values. The
        database host is automatically resolved to handle running outside
        Docker containers.

        Example:
            >>> tracker = HasuraTableTracker()
            >>> custom_tracker = HasuraTableTracker(
            ...     db_host="localhost",
            ...     db_port=5433
            ... )

        """
        self.client = hasura_client or HasuraClient()

        settings = HasuraPostgresSettings()
        self.db_host, self.db_port = resolve_host(
            db_host or settings.postgres_host,
            db_port or settings.postgres_port,
            port_env_var="POSTGRES_PORT",
        )
        self.db_name = db_name or settings.postgres_db
        self.db_user = db_user or settings.postgres_user
        self.db_password = db_password or settings.postgres_password

    def _get_db_connection(self):
        """Return a psycopg2 connection in autocommit mode.

        The connection is configured with the resolved host and port.
        Raises psycopg2.Error if the connection fails.

        Example:
            >>> conn = tracker._get_db_connection()
            >>> cursor = conn.cursor()
            >>> cursor.execute("SELECT version()")

        """
        conn = psycopg2.connect(
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            user=self.db_user,
            password=self.db_password,
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        return conn

    def discover_user_schemas(self) -> list[str]:
        """Return the sorted names of non-system schemas containing base tables.

        Schemas named like pg_* and information_schema are excluded.
        Raises psycopg2.Error if the database query fails.

        Example:
            >>> schemas = tracker.discover_user_schemas()
            >>> print(schemas)
            ['api', 'marts', 'public']

        """
        conn = self._get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                """
                SELECT DISTINCT table_schema
                FROM information_schema.tables
                WHERE table_type = 'BASE TABLE'
                  AND table_schema NOT LIKE 'pg_%%'
                  AND table_schema != 'information_schema'
                ORDER BY table_schema
                """
            )
            return [row[0] for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    def get_tables_in_schema(self, schema: str) -> list[str]:
        """Return the sorted base-table names in the given schema.

        Raises psycopg2.Error if the database query fails.

        Example:
            >>> tables = tracker.get_tables_in_schema("api")
            >>> print(tables)
            ['customers', 'orders', 'products']

        """
        conn = self._get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = %s AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """,
                (schema,),
            )

            return [row[0] for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    def get_foreign_keys(self, schema: str, table: str) -> list[dict]:
        """Return the foreign key constraints defined on a table.

        Each result dict carries local_column, ref_schema, ref_table, and
        ref_column. Raises psycopg2.Error if the database query fails.

        Example:
            >>> fks = tracker.get_foreign_keys("api", "orders")
            >>> for fk in fks:
            ...     print(f"{fk['local_column']} -> {fk['ref_table']}.{fk['ref_column']}")
            customer_id -> customers.id

        """
        conn = self._get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                """
                SELECT
                    kcu.column_name,
                    ccu.table_schema,
                    ccu.table_name,
                    ccu.column_name
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage AS ccu
                    ON ccu.constraint_name = tc.constraint_name
                    AND ccu.table_schema = tc.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                    AND tc.table_schema = %s
                    AND tc.table_name = %s
                ORDER BY kcu.column_name
            """,
                (schema, table),
            )

            fks = []
            for local_col, ref_schema, ref_table, ref_col in cursor.fetchall():
                fks.append(
                    {
                        "local_column": local_col,
                        "ref_schema": ref_schema,
                        "ref_table": ref_table,
                        "ref_column": ref_col,
                    }
                )

            return fks
        finally:
            cursor.close()
            conn.close()

    def track_tables(
        self, schema: str, exclude: list[str] | None = None, verbose: bool = True
    ) -> dict[str, bool]:
        """Track all tables in a schema and report per-table success.

        Discovers the schema's tables (skipping any named in exclude) and
        tracks each in Hasura. Returns a dict mapping table name to success
        boolean. Raises requests.RequestException on Hasura API failure or
        psycopg2.Error if database queries fail.

        Example:
            >>> results = tracker.track_tables("api")
            >>> print(f"Tracked {sum(results.values())}/{len(results)} tables")
            >>> results = tracker.track_tables("api", exclude=["temp_table"])

        """
        if verbose:
            logger.info("Discovering tables in schema '%s'...", schema)

        tables = self.get_tables_in_schema(schema)
        exclude = exclude or []
        tables = [t for t in tables if t not in exclude]

        if verbose:
            logger.info("Found %s tables", len(tables))

        results = {}
        for table in tables:
            try:
                if verbose:
                    logger.info("Tracking %s.%s...", schema, table)

                self.client.track_table(schema, table)
                results[table] = True

                if verbose:
                    logger.info("Tracking %s.%s ✓", schema, table)
            except Exception as e:
                results[table] = False
                if verbose:
                    logger.warning("Tracking %s.%s ✗ (%s)", schema, table, str(e)[:200])

        return results

    def setup_relationships(self, schema: str, verbose: bool = True) -> dict[tuple[str, str], bool]:
        """Create Hasura object relationships from foreign keys.

        Discovers foreign keys on every table in the schema and creates an
        object relationship per key; relationship names drop the "_id"
        suffix from the local column. Returns a dict mapping
        (table, relationship_name) to success boolean. Raises
        requests.RequestException on Hasura API failure or psycopg2.Error
        if database queries fail.

        Example:
            >>> results = tracker.setup_relationships("api")
            >>> for (table, rel), success in results.items():
            ...     status = "created" if success else "failed"
            ...     print(f"{table}.{rel}: {status}")

        """
        tables = self.get_tables_in_schema(schema)
        results = {}

        for table in tables:
            fks = self.get_foreign_keys(schema, table)

            for fk in fks:
                rel_name = fk["local_column"].replace("_id", "")

                try:
                    if verbose:
                        logger.info(
                            "Creating relationship %s.%s -> %s...",
                            table,
                            rel_name,
                            fk["ref_table"],
                        )

                    self.client.create_object_relationship(
                        schema,
                        table,
                        rel_name,
                        manual_configuration={
                            "foreign_key_constraint_on": fk["local_column"],
                        },
                    )

                    results[(table, rel_name)] = True
                    if verbose:
                        logger.info("Creating relationship %s.%s ✓", table, rel_name)
                except Exception as e:
                    results[(table, rel_name)] = False
                    if verbose:
                        logger.warning(
                            "Creating relationship %s.%s ✗ (%s)", table, rel_name, str(e)[:200]
                        )

        return results

    def setup_default_permissions(
        self, schema: str, verbose: bool = True
    ) -> dict[tuple[str, str], bool]:
        """Create default SELECT permissions on every table in the schema.

        Roles anon, analyst, and admin each get SELECT permissions; the
        'anon' role allows aggregations. Returns a dict mapping
        (table, role) to success boolean. Raises requests.RequestException
        on Hasura API failure or psycopg2.Error if database queries fail.

        Example:
            >>> results = tracker.setup_default_permissions("api")
            >>> print(f"Created {sum(results.values())} permissions")

        """
        tables = self.get_tables_in_schema(schema)
        results = {}

        # Default: allow anon users to view api schema
        default_permissions = [
            ("anon", {"allow_aggregations": True}),
            ("analyst", {}),
            ("admin", {}),
        ]

        for table in tables:
            for role, filter_expr in default_permissions:
                try:
                    if verbose:
                        logger.info("Creating permission %s.%s...", table, role)

                    self.client.create_select_permission(schema, table, role, filter=filter_expr)

                    results[(table, role)] = True
                    if verbose:
                        logger.info("Creating permission %s.%s ✓", table, role)
                except Exception as e:
                    results[(table, role)] = False
                    if verbose:
                        logger.warning(
                            "Creating permission %s.%s ✗ (%s)", table, role, str(e)[:200]
                        )

        return results


def auto_track(schema: str = "api", verbose: bool = True) -> dict[str, Any]:
    """Fully auto-configure a schema: track tables, relationships, permissions.

    Returns a dict with "tables", "relationships", and "permissions" keys,
    each mapping its target (table name, (table, relationship) pair, or
    (table, role) pair) to a success boolean. Raises
    requests.RequestException on Hasura API failure or psycopg2.Error if
    database queries fail.

    Example:
        >>> results = auto_track("api")
        >>> print(f"Tables: {sum(results['tables'].values())}/{len(results['tables'])}")

    """
    if verbose:
        logger.info("=" * 60)
        logger.info("Hasura Auto-Track")
        logger.info("=" * 60)

    tracker = HasuraTableTracker()

    # Track tables
    track_results = tracker.track_tables(schema, verbose=verbose)
    if verbose:
        logger.info("")

    # Setup relationships
    if verbose:
        logger.info("Setting up relationships...")
    rel_results = tracker.setup_relationships(schema, verbose=verbose)
    if verbose:
        logger.info("")

    # Setup default permissions
    if verbose:
        logger.info("Setting up default permissions...")
    perm_results = tracker.setup_default_permissions(schema, verbose=verbose)

    if verbose:
        logger.info("=" * 60)
        logger.info("✓ Auto-track completed")
        logger.info(
            "  Tables tracked: %s/%s",
            sum(1 for v in track_results.values() if v),
            len(track_results),
        )
        logger.info(
            "  Relationships: %s/%s",
            sum(1 for v in rel_results.values() if v),
            len(rel_results),
        )
        logger.info(
            "  Permissions: %s/%s",
            sum(1 for v in perm_results.values() if v),
            len(perm_results),
        )
        logger.info("=" * 60)

    return {
        "tables": track_results,
        "relationships": rel_results,
        "permissions": perm_results,
    }


def auto_track_all(verbose: bool = True) -> dict[str, dict[str, Any]]:
    """Auto-configure every user schema via auto_track.

    Discovers all non-system schemas containing tables and runs auto_track
    on each, returning a dict mapping schema name to that schema's tracking
    results. Raises requests.RequestException on Hasura API failure or
    psycopg2.Error if database queries fail.

    Example:
        >>> all_results = auto_track_all()
        >>> for schema, results in all_results.items():
        ...     tracked = sum(results['tables'].values())
        ...     print(f"{schema}: {tracked} tables tracked")

    """
    if verbose:
        logger.info("=" * 60)
        logger.info("Hasura Auto-Track (All Schemas)")
        logger.info("=" * 60)

    tracker = HasuraTableTracker()
    schemas = tracker.discover_user_schemas()

    if verbose:
        logger.info("Discovered %d user schemas: %s", len(schemas), ", ".join(schemas))
        logger.info("")

    results: dict[str, dict[str, Any]] = {}
    for schema in schemas:
        if verbose:
            logger.info("Processing schema: %s", schema)
        results[schema] = auto_track(schema=schema, verbose=verbose)

    if verbose:
        logger.info("=" * 60)
        logger.info("✓ All schemas processed")
        total_tables = sum(len(r.get("tables", {})) for r in results.values())
        tracked_tables = sum(
            sum(1 for v in r.get("tables", {}).values() if v) for r in results.values()
        )
        logger.info("  Total tables tracked: %d/%d", tracked_tables, total_tables)
        logger.info("=" * 60)

    return results
