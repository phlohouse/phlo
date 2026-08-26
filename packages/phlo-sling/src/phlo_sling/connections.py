"""Auto-generate Sling connections from Phlo capability metadata.

Discovers and configures Sling connections based on installed Phlo package
capabilities, resolving Postgres, Iceberg, and S3-compatible object-store
connections by inspecting the configuration of related Phlo packages.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from typing import Any

from phlo.capabilities import list_capabilities, resolve_capability
from phlo.infrastructure.config import load_project_config
from phlo.logging import get_logger
from phlo_sling.settings import get_settings

logger = get_logger(__name__)


def _resolve_clickhouse_connection() -> dict[str, dict[str, Any]]:
    """Resolve ClickHouse connection from phlo-clickhouse settings.

    Builds a Sling-compatible connection (native protocol) from the installed
    phlo-clickhouse package settings; returns {"PHLO_CLICKHOUSE": ...} or an
    empty dict when phlo-clickhouse is not installed or configured.
    """
    try:
        from phlo_clickhouse.settings import get_settings as get_ch_settings

        ch = get_ch_settings()
        return {
            "PHLO_CLICKHOUSE": {
                "type": "clickhouse",
                "host": ch.clickhouse_host,
                "port": ch.clickhouse_native_port,
                "database": ch.clickhouse_db,
                "user": ch.clickhouse_user,
                "password": ch.clickhouse_password,
            }
        }
    except (ImportError, Exception) as exc:
        logger.debug("clickhouse_connection_skipped", error=str(exc))
        return {}


def _resolve_delta_connection() -> dict[str, dict[str, Any]]:
    """Resolve a Delta Lake connection from phlo-delta settings.

    Sling targets Delta via its native file target rooted at the warehouse;
    returns {"PHLO_DELTA": ...} or an empty dict when phlo-delta is not
    installed.

    The type label must be ``file``: that is the connection type Sling
    registers for filesystem roots, and an unregistered type makes Sling
    drop the connection entirely (``could not find connection PHLO_DELTA``).
    """
    try:
        from phlo_delta.settings import get_settings as get_delta_settings

        delta = get_delta_settings()
        return {
            "PHLO_DELTA": {
                "type": "file",
                "root_path": delta.delta_warehouse_path,
            }
        }
    except (ImportError, Exception) as exc:
        logger.debug("delta_connection_skipped", error=str(exc))
        return {}


def resolve_phlo_connections() -> dict[str, dict[str, Any]]:
    """Build Sling connection definitions from installed Phlo package settings.

    Inspects known Phlo capability providers (phlo-postgres, phlo-minio, etc.)
    and returns a dict mapping connection name to a Sling-compatible config with
    connection-specific parameters (host, port, credentials, endpoint URLs),
    so Phlo-managed infrastructure can back Sling replications without manual
    connection configuration.

    Example:
        Get auto-discovered connections::

            connections = resolve_phlo_connections()
            # {"PHLO_POSTGRES": {"type": "postgres", "host": "...", ...}}
    """
    if not get_settings().sling_auto_connections:
        logger.debug("sling_auto_connections_disabled")
        return {}

    connections: dict[str, dict[str, Any]] = {}

    connections.update(_resolve_postgres_connection())
    connections.update(_resolve_iceberg_connection())
    connections.update(_resolve_s3_connection())
    connections.update(_resolve_clickhouse_connection())
    connections.update(_resolve_delta_connection())

    return connections


def _project_env_value(name: str) -> str | None:
    """Read a non-secret default from phlo.yaml env: when host os.environ lacks it.

    Falls back to the project configuration file for ``name`` when the variable
    is not set in the actual environment; returns the phlo.yaml value when found
    and valid, otherwise None.

    """
    try:
        project_config = load_project_config()
    except Exception as exc:
        logger.debug("project_env_lookup_failed", name=name, error=str(exc))
        return None

    env_config = project_config.get("env", {})
    if not isinstance(env_config, dict):
        return None

    value = env_config.get(name)
    return value if isinstance(value, str) and value else None


def _ensure_capabilities_discovered(*kinds: str) -> None:
    """Populate the capability registry only when the requested kinds are absent.

    Lazily discovers capabilities from installed Phlo packages when none of the
    requested capability kinds are already registered.
    """
    if any(list_capabilities(kind) for kind in kinds):
        return

    from phlo.capabilities.discovery import discover_capabilities

    discover_capabilities()


def _get_iceberg_settings():
    """Import phlo-iceberg settings lazily for optional package installs.

    Lazily imports phlo-iceberg settings so installs without the optional
    package do not fail at import time. Raises ImportError when phlo-iceberg is
    not installed; otherwise returns the settings instance.

    """
    from phlo_iceberg.settings import get_settings as get_iceberg_settings

    return get_iceberg_settings()


def _resolve_postgres_connection() -> dict[str, dict[str, Any]]:
    """Resolve Postgres connection from phlo-postgres settings.

    Builds a Sling-compatible connection configuration from the installed
    phlo-postgres package settings if available; returns {"PHLO_POSTGRES": ...}
    or an empty dict when phlo-postgres is not installed or configured.

    """
    try:
        from phlo_postgres.settings import get_settings as get_pg_settings

        pg = get_pg_settings()
        return {
            "PHLO_POSTGRES": {
                "type": "postgres",
                "host": pg.postgres_host,
                "port": pg.postgres_port,
                "database": pg.postgres_db,
                "user": pg.postgres_user,
                "password": pg.postgres_password,
                "schema": getattr(pg, "postgres_schema", "public"),
            }
        }
    except (ImportError, Exception) as exc:
        logger.debug("postgres_connection_skipped", error=str(exc))
        return {}


def _resolve_iceberg_connection() -> dict[str, dict[str, Any]]:
    """Resolve an Iceberg REST catalog connection from phlo-iceberg settings.

    Builds a Sling-compatible connection configuration for Iceberg REST catalog
    access from the installed phlo-iceberg package settings; returns
    {"PHLO_ICEBERG": ...} or an empty dict when phlo-iceberg is not installed or
    configured.

    """
    try:
        settings = _get_iceberg_settings()
        ref = settings.iceberg_default_ref
        config = settings.get_pyiceberg_catalog_config(ref)
        return {
            "PHLO_ICEBERG": {
                "type": "iceberg",
                "catalog_type": "rest",
                "rest_uri": config["uri"],
                "rest_warehouse": config["warehouse"],
                "s3_endpoint": config["s3.endpoint"],
                "s3_access_key_id": config["s3.access-key-id"],
                "s3_secret_access_key": config["s3.secret-access-key"],
                "s3_region": config["s3.region"],
                "schema": settings.iceberg_default_namespace,
            }
        }
    except (ImportError, Exception) as exc:
        logger.debug("iceberg_connection_skipped", error=str(exc))
        return {}


def _resolve_s3_connection() -> dict[str, dict[str, Any]]:
    """Resolve S3 connection from the active object-store capability.

    Discovers and builds a Sling-compatible S3 connection configuration from the
    active object-store capability provider (e.g., phlo-minio); returns
    {"PHLO_S3": ...} or an empty dict when no object-store capability is
    available.

    """
    _ensure_capabilities_discovered("object_store")
    requested_name = os.environ.get("PHLO_OBJECT_STORE") or _project_env_value("PHLO_OBJECT_STORE")
    resolution = resolve_capability("object_store", requested_name)
    if resolution is None:
        available = list_capabilities("object_store")
        logger.debug(
            "object_store_connection_skipped",
            requested_name=requested_name,
            available=available,
        )
        return {}

    provider = resolution.provider
    if hasattr(provider, "to_sling_connection"):
        config = provider.to_sling_connection()
    else:
        config = {
            key: value
            for key, value in resolution.metadata.items()
            if key in {"type", "endpoint", "access_key_id", "secret_access_key", "region"}
        }

    if not config:
        logger.debug(
            "object_store_connection_missing_config",
            capability_name=resolution.name,
        )
        return {}

    return {"PHLO_S3": config}


def export_sling_env(connections: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Convert connection dicts to Sling environment variable format.

    Sling expects connections as environment variables with JSON values; returns
    a dict mapping each connection name to its JSON-serialized config.

    Example:
        Export connections for environment setup::

            conns = resolve_phlo_connections()
            env_vars = export_sling_env(conns)
            # {"PHLO_POSTGRES": '{"type": "postgres", ...}'}

    """
    import json

    env_vars: dict[str, str] = {}
    for name, config in connections.items():
        env_vars[name] = json.dumps(config)
    return env_vars


def apply_sling_connection_env(environ: MutableMapping[str, str] | None = None) -> dict[str, str]:
    """Inject resolved Sling connections into an environment mapping.

    Applies auto-discovered Sling connections to ``environ`` (default
    ``os.environ``), preserving existing variables rather than overwriting them;
    returns the injected environment variables.

    Example:
        Apply connections before running Sling::

            apply_sling_connection_env()
            # Now os.environ contains PHLO_POSTGRES, PHLO_S3, etc.

    """
    target_env = os.environ if environ is None else environ
    env_vars = export_sling_env(resolve_phlo_connections())
    for name, value in env_vars.items():
        target_env.setdefault(name, value)
    return env_vars
