"""PostgREST authentication infrastructure setup.

This module sets up the core PostgREST authentication infrastructure:
- PostgreSQL extensions (pgcrypto)
- Auth schema and users table
- JWT signing/verification functions
- Database roles (anon, authenticated, analyst, admin)
- Row-Level Security policies

Usage:
    From CLI:
        $ phlo postgrest setup-auth

    From Python:
        >>> from phlo_postgrest import setup_postgrest
        >>> setup_postgrest()
"""

from pathlib import Path
from typing import Optional

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from phlo.config.env import load_project_env
from phlo.logging import get_logger, setup_logging

logger = get_logger(__name__)


def get_db_connection(
    host: Optional[str] = None,
    port: Optional[int] = None,
    database: Optional[str] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
):
    """Open an autocommit psycopg2 connection.

    Each parameter falls back to its POSTGRES_* environment variable, then
    to the built-in default (localhost:5432/lakehouse as user "lake").
    """
    env = load_project_env()
    conn_params = {
        "host": host or env.get("POSTGRES_HOST", "localhost"),
        "port": port or int(env.get("POSTGRES_PORT", "5432")),
        "database": database or env.get("POSTGRES_DB", "lakehouse"),
        "user": user or env.get("POSTGRES_USER", "lake"),
        "password": password or env.get("POSTGRES_PASSWORD", "lakepass"),
    }

    conn = psycopg2.connect(**conn_params)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    return conn


def execute_sql_file(conn, filepath: Path, verbose: bool = True):
    """Execute a SQL file on ``conn``, logging and re-raising on failure."""
    if verbose:
        logger.info("Executing: %s", filepath.name)
    with open(filepath, "r") as f:
        sql_content = f.read()

    cursor = conn.cursor()
    try:
        cursor.execute(sql_content)
        if verbose:
            logger.info("✓ %s completed successfully", filepath.name)
    except Exception as e:
        logger.error("✗ %s failed: %s", filepath.name, e)
        raise
    finally:
        cursor.close()


def check_if_setup_complete(conn) -> bool:
    """Return True when both the auth schema and authenticator role exist."""
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.schemata
                WHERE schema_name = 'auth'
            );
        """)
        auth_exists = cursor.fetchone()[0]

        # Check if authenticator role exists
        cursor.execute("""
            SELECT EXISTS (
                SELECT 1 FROM pg_roles
                WHERE rolname = 'authenticator'
            );
        """)
        role_exists = cursor.fetchone()[0]

        return auth_exists and role_exists
    finally:
        cursor.close()


def setup_postgrest(
    host: Optional[str] = None,
    port: Optional[int] = None,
    database: Optional[str] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
    force: bool = False,
    verbose: bool = True,
):
    """Set up PostgREST authentication infrastructure.

    Idempotent: skips setup when already complete unless ``force`` is set.
    Connection parameters follow get_db_connection precedence; ``user``
    must have superuser privileges. Applies every SQL file in the bundled
    sql/ directory in sorted order.

    Example:
        >>> from phlo_postgrest import setup_postgrest
        >>> setup_postgrest()
    """
    if verbose:
        logger.info("=" * 50)
        logger.info("PostgREST Authentication Infrastructure Setup")
        logger.info("=" * 50)

    # Get database connection
    conn = get_db_connection(host, port, database, user, password)

    if verbose:
        cursor = conn.cursor()
        cursor.execute("SELECT current_database(), current_user;")
        db, usr = cursor.fetchone()
        logger.info("Database: %s", db)
        logger.info("User: %s", usr)
        cursor.close()
        logger.info("=" * 50)

    # Check if already setup
    if not force and check_if_setup_complete(conn):
        if verbose:
            logger.info("✓ PostgREST infrastructure already set up.")
            logger.info("  Use force=True to re-apply setup.")
        conn.close()
        return

    # Get SQL files directory
    sql_dir = Path(__file__).parent / "sql"

    # Execute SQL files in order
    sql_files = sorted(sql_dir.glob("*.sql"))

    for sql_file in sql_files:
        execute_sql_file(conn, sql_file, verbose)
        if verbose:
            logger.info("")

    conn.close()

    if verbose:
        logger.info("=" * 50)
        logger.info("✓ PostgREST setup completed successfully!")
        logger.info("=" * 50)
        logger.info("Next steps:")
        logger.info("  1. Create your API views in the 'api' schema")
        logger.info("  2. Start PostgREST: docker-compose up -d postgrest")
        logger.info("  3. Test login: curl -X POST http://localhost:10018/rpc/login \\")
        logger.info("       -H 'Content-Type: application/json' \\")
        logger.info('       -d \'{"username": "analyst", "password": "<redacted>"}\'')


if __name__ == "__main__":
    setup_logging()
    setup_postgrest()
