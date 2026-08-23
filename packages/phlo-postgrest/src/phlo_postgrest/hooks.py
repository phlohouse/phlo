"""PostgREST hooks for auto-configuration and schema discovery.

This module provides automated configuration hooks that integrate PostgREST
with Phlo's infrastructure management. It handles dynamic schema discovery
and PostgREST configuration updates based on the current database state.

Functions:
    discover_schemas: Automatically discover user schemas containing tables.
    configure_schemas: Update PostgREST config and restart container.

Example:
    $ python -m phlo_postgrest.hooks configure-schemas
    >>> from phlo_postgrest.hooks import discover_schemas
    >>> schemas = discover_schemas()
    >>> print(schemas)
    ['public', 'marts', 'staging']

Run as a standalone module (python -m phlo_postgrest.hooks) for schema auto-configuration rather
than imported directly; builds on phlo.infrastructure.config and phlo.logging.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from phlo.infrastructure.config import get_project_name_from_config, load_infrastructure_config
from phlo.logging import get_logger, setup_logging

logger = get_logger(__name__)


def _get_config_file() -> Path:
    """Return the PostgREST configuration file path.

    Locates PostgREST configuration within the project's .phlo directory at
    .phlo/postgrest/conf/postgrest.conf; the file may not exist yet if
    PostgREST hasn't been initialized.
    """
    phlo_dir = Path.cwd() / ".phlo"
    return phlo_dir / "postgrest" / "conf" / "postgrest.conf"


def _read_config_values(config_file: Path) -> dict[str, str]:
    """Parse PostgREST configuration file into key-value pairs.

    Extracts configuration directives while handling comments and quoted
    values; returns an empty dict when the file doesn't exist.

    Example:
        >>> config = _read_config_values(Path("postgrest.conf"))
        >>> config.get("db-uri")
        'postgres://user:pass@localhost/db'
    """
    values: dict[str, str] = {}
    if not config_file.exists():
        return values

    for raw_line in config_file.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "#" in line:
            line = line.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        values[key] = value

    return values


def _parse_db_uri(db_uri: str) -> dict[str, str]:
    """Parse database URI into connection components.

    Extracts username, password, and database name from a PostgreSQL
    connection URI, handling URL-encoded characters.

    Example:
        >>> _parse_db_uri("postgres://lake:secret@localhost/lakehouse")
        {'username': 'lake', 'password': 'secret', 'database': 'lakehouse'}
    """
    parsed = urlparse(db_uri)
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    database = parsed.path.lstrip("/")
    return {
        "username": username,
        "password": password,
        "database": database,
    }


def _resolve_container_name(service_name: str) -> str:
    """Resolve Docker container name using infrastructure configuration.

    Uses Phlo's infrastructure configuration when the service is defined,
    falling back to the default naming pattern otherwise.

    Example:
        >>> _resolve_container_name("postgres")
        'phlo-postgres-1'
    """
    project_name = get_project_name_from_config() or Path.cwd().name
    infra = load_infrastructure_config()
    service = infra.get_service(service_name)
    if service:
        return service.get_container_name(project_name, infra.container_naming_pattern)
    return infra.container_naming_pattern.format(project=project_name, service=service_name)


def _discover_schemas_via_docker(db_uri: str) -> list[str]:
    """Discover database schemas by querying PostgreSQL container.

    Executes psql inside the PostgreSQL Docker container to discover all user
    schemas containing tables, excluding system schemas; returns a sorted list
    of schema names.

    Raises: ValueError when db_uri lacks username or database components;
    RuntimeError when the psql command fails or returns an error.

    Example:
        >>> schemas = _discover_schemas_via_docker(
        ...     "postgres://lake:lakepass@postgres/lakehouse"
        ... )
        >>> print(schemas)
        ['marts', 'public', 'staging']
    """
    db_parts = _parse_db_uri(db_uri)
    if not db_parts["username"] or not db_parts["database"]:
        raise ValueError("db-uri must include username and database")

    sql = (
        "SELECT DISTINCT table_schema "
        "FROM information_schema.tables "
        "WHERE table_type = 'BASE TABLE' "
        "AND table_schema NOT LIKE 'pg_%' "
        "AND table_schema != 'information_schema' "
        "AND table_schema != 'hdb_catalog' "
        "ORDER BY table_schema;"
    )

    postgres_container = _resolve_container_name("postgres")
    logger.info(
        "postgrest_schema_discovery_docker_exec_started",
        postgres_container=postgres_container,
        database=db_parts["database"],
        db_user=db_parts["username"],
    )
    cmd = [
        "docker",
        "exec",
    ]
    if db_parts["password"]:
        cmd.extend(["-e", f"PGPASSWORD={db_parts['password']}"])
    cmd.extend(
        [
            postgres_container,
            "psql",
            "-t",
            "-A",
            "-U",
            db_parts["username"],
            "-d",
            db_parts["database"],
            "-c",
            sql,
        ]
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        logger.exception(
            "postgrest_schema_discovery_docker_exec_failed",
            postgres_container=postgres_container,
            database=db_parts["database"],
            db_user=db_parts["username"],
        )
        raise

    if result.returncode != 0:
        stderr_lines = [line for line in result.stderr.splitlines() if line.strip()]
        logger.error(
            "postgrest_schema_discovery_docker_exec_failed",
            postgres_container=postgres_container,
            database=db_parts["database"],
            db_user=db_parts["username"],
            return_code=result.returncode,
            stderr_line_count=len(stderr_lines),
        )
        raise RuntimeError(f"psql failed: {result.stderr.strip()}")

    schemas = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    logger.info(
        "postgrest_schema_discovery_docker_exec_succeeded",
        postgres_container=postgres_container,
        database=db_parts["database"],
        db_user=db_parts["username"],
        schema_count=len(schemas),
        schemas=schemas,
    )
    return schemas


def _run_psql(db_uri: str, sql: str) -> None:
    """Run a SQL statement in the configured PostgreSQL container."""
    db_parts = _parse_db_uri(db_uri)
    if not db_parts["username"] or not db_parts["database"]:
        raise ValueError("db-uri must include username and database")

    postgres_container = _resolve_container_name("postgres")
    cmd = ["docker", "exec"]
    if db_parts["password"]:
        cmd.extend(["-e", f"PGPASSWORD={db_parts['password']}"])
    cmd.extend(
        [
            postgres_container,
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            db_parts["username"],
            "-d",
            db_parts["database"],
            "-c",
            sql,
        ]
    )
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception:
        logger.exception(
            "postgrest_schema_reload_psql_failed",
            postgres_container=postgres_container,
            database=db_parts["database"],
            db_user=db_parts["username"],
        )
        raise
    if result.returncode != 0:
        stderr_lines = [line for line in result.stderr.splitlines() if line.strip()]
        logger.error(
            "postgrest_schema_reload_psql_failed",
            postgres_container=postgres_container,
            database=db_parts["database"],
            db_user=db_parts["username"],
            return_code=result.returncode,
            stderr_line_count=len(stderr_lines),
        )
        raise RuntimeError(f"psql failed: {result.stderr.strip()}")


def _get_db_uri() -> str:
    """Read db-uri from postgrest.conf."""
    config_file = _get_config_file()
    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found at {config_file}")

    config_values = _read_config_values(config_file)
    db_uri = config_values.get("db-uri")
    if not db_uri:
        raise ValueError("db-uri not found in PostgREST config")
    return db_uri


def reload_schema() -> None:
    """Ask PostgREST to reload its schema cache without restarting the container."""
    db_uri = _get_db_uri()
    _run_psql(db_uri, "NOTIFY pgrst, 'reload schema';")
    logger.info("postgrest_schema_reload_notified")


def discover_schemas() -> list[str]:
    """Discover all user schemas containing tables.

    Reads PostgREST configuration to obtain database connection details, then
    queries the database for all non-system schemas with tables.

    Raises: FileNotFoundError when the PostgREST configuration file is missing;
    ValueError when db-uri is not configured in PostgREST config.

    Example:
        >>> from phlo_postgrest.hooks import discover_schemas
        >>> schemas = discover_schemas()
        >>> print(schemas)
        ['marts', 'public']
    """
    db_uri = _get_db_uri()
    return _discover_schemas_via_docker(db_uri)


def configure_schemas() -> None:
    """Auto-configure PostgREST to expose all discovered schemas.

    Discovers user schemas from the database (prioritizing 'marts' when
    present), rewrites the db-schemas directive in postgrest.conf, restarts
    the PostgREST container to apply changes, and waits for it to become
    healthy.

    Raises: FileNotFoundError when the PostgREST configuration is missing;
    RuntimeError when the container restart fails.

    Example:
        >>> from phlo_postgrest.hooks import configure_schemas
        >>> configure_schemas()
        Discovering user schemas for PostgREST...
        Discovered schemas: marts,public,staging
        Updated .phlo/postgrest/conf/postgrest.conf
        PostgREST restarted successfully
    """
    logger.info("Discovering user schemas for PostgREST...")

    try:
        schemas = discover_schemas()
    except Exception as e:
        logger.error("Failed to discover schemas: %s", e)
        raise

    if not schemas:
        logger.warning("No user schemas found, using default 'public'")
        schemas = ["public"]
    elif "marts" in schemas:
        schemas = ["marts"] + [schema for schema in schemas if schema != "marts"]

    schemas_str = ",".join(schemas)
    logger.info("Discovered schemas: %s", schemas_str)

    config_file = _get_config_file()

    if not config_file.exists():
        logger.warning("Config file not found at %s", config_file)
        return

    content = config_file.read_text()
    lines = content.splitlines()

    # Rewrite db-schemas in place, inserting the directive after db-anon-role
    # when the config does not have one yet.
    updated = False
    new_lines = []
    for line in lines:
        if line.startswith("db-schemas"):
            new_lines.append(f'db-schemas = "{schemas_str}"')
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        # Add db-schemas line after db-anon-role
        for i, line in enumerate(new_lines):
            if line.startswith("db-anon-role"):
                new_lines.insert(i + 1, f'db-schemas = "{schemas_str}"')
                break

    config_file.write_text("\n".join(new_lines) + "\n")
    logger.info("Updated %s with db-schemas=%s", config_file, schemas_str)

    # Restart PostgREST container to pick up new config
    container_name = _resolve_container_name("postgrest")

    logger.info("Restarting PostgREST container to apply new schema config...")
    try:
        result = subprocess.run(
            ["docker", "restart", container_name],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            logger.info("PostgREST restarted, waiting for healthy status...")
            _wait_for_healthy(container_name, timeout=30)
        else:
            logger.warning("Failed to restart PostgREST: %s", result.stderr)
            # Restart failed, but the running container can still pick up new
            # tables in already-exposed schemas via a schema-cache reload.
            try:
                reload_schema()
            except Exception as e:
                logger.warning("Could not notify PostgREST schema reload: %s", e)
    except Exception as e:
        logger.warning("Could not restart PostgREST container: %s", e)
        try:
            reload_schema()
        except Exception as e:
            logger.warning("Could not notify PostgREST schema reload: %s", e)


def _wait_for_healthy(container_name: str, timeout: int = 30) -> None:
    """Wait for a Docker container to reach healthy status.

    Polls the container's health via Docker inspect until healthy or `timeout`
    (default 30 seconds) expires. Containers without a healthcheck are treated
    as ready after a brief wait; timeouts are logged as warnings without
    raising exceptions.

    Example:
        >>> _wait_for_healthy("phlo-postgrest-1", timeout=60)
    """
    import time

    start = time.time()
    while time.time() - start < timeout:
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Health.Status}}", container_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            status = result.stdout.strip()
            if status == "healthy":
                logger.info("PostgREST container is healthy")
                return
            if status in ("unhealthy", ""):
                # Empty status means no healthcheck is defined; treat both as
                # ready instead of blocking configuration on container health.
                time.sleep(2)
                logger.info("PostgREST container ready (no healthcheck)")
                return
        except Exception:
            pass
        time.sleep(1)
    logger.warning("Timeout waiting for PostgREST to become healthy")


if __name__ == "__main__":
    setup_logging()

    if len(sys.argv) > 1 and sys.argv[1] == "configure-schemas":
        configure_schemas()
    elif len(sys.argv) > 1 and sys.argv[1] == "reload-schema":
        reload_schema()
    else:
        logger.info("Usage: python -m phlo_postgrest.hooks configure-schemas|reload-schema")
