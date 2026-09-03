"""PostgreSQL connection settings and configuration.

This module provides Pydantic-based settings management for PostgreSQL connections,
including support for connection string generation and host/port resolution via
the phlo configuration system.

Example:
    >>> from phlo_postgres.settings import get_settings
    >>> settings = get_settings()
    >>> conn_str = settings.get_postgres_connection_string()
    >>> print(conn_str)
    postgresql://phlo:phlo@postgres:5432/phlo


    Settings for the PostgreSQL service, built on the shared phlo.config base/cache/network helpers.
    Loaded lazily via get_settings(); phlo_sling pulls its Postgres connection settings at runtime.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from pydantic import Field

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached
from phlo.config.network import resolve_host


class PostgresSettings(BaseConfig):
    """PostgreSQL database connection and schema configuration managed with
    Pydantic validation.

    Supports environment variable overrides and provides utilities for building
    connection strings. Fields cover the host (with special resolution rules),
    port, user, password (URL-encoded in connection strings), default database,
    and the schema for published mart tables.

    Example:
        >>> settings = PostgresSettings()
        >>> print(settings.postgres_host)
        postgres
        >>> conn_str = settings.get_postgres_connection_string()
        >>> print(conn_str)
        postgresql://phlo:phlo@postgres:5432/phlo
        >>>
        >>> # Override defaults
        >>> custom = PostgresSettings(
        ...     postgres_host="prod.db.internal",
        ...     postgres_user="admin",
        ...     postgres_password="secret123"
        ... )

    """

    postgres_host: str = Field(default="postgres", description="PostgreSQL host")
    postgres_port: int = Field(default=5432, description="PostgreSQL port")
    postgres_user: str = Field(default="phlo", description="PostgreSQL username")
    postgres_password: str = Field(default="phlo", description="PostgreSQL password")
    postgres_db: str = Field(default="phlo", description="PostgreSQL database name")
    postgres_mart_schema: str = Field(
        default="marts", description="Schema for published mart tables"
    )

    def model_post_init(self, __context: Any) -> None:
        """Resolve host and port after initialization via phlo's network
        resolution system, honoring environment overrides such as POSTGRES_PORT;
        values are stored through object.__setattr__ to bypass Pydantic's
        frozen-model behavior.

        """
        host, port = resolve_host(
            self.postgres_host, self.postgres_port, port_env_var="POSTGRES_PORT"
        )
        object.__setattr__(self, "postgres_host", host)
        object.__setattr__(self, "postgres_port", port)

    def get_postgres_connection_string(self, include_db: bool = True) -> str:
        """Build a URL-encoded PostgreSQL connection URI suitable for SQLAlchemy,
        psycopg2, or other database libraries.

        Pass ``include_db=False`` to omit the database name, for example when
        creating the database or specifying it separately.

        Example:
            >>> settings = PostgresSettings()
            >>> settings.get_postgres_connection_string()
            'postgresql://phlo:phlo@postgres:5432/phlo'
            >>>
            >>> # Without database (for server-level operations)
            >>> settings.get_postgres_connection_string(include_db=False)
            'postgresql://phlo:phlo@postgres:5432'
            >>>
            >>> # With special characters in password
            >>> settings = PostgresSettings(postgres_password="p@ssw/rd")
            >>> settings.get_postgres_connection_string()
            'postgresql://phlo:p%40ssw%2Frd@postgres:5432/phlo'

        """
        db_part = f"/{self.postgres_db}" if include_db else ""
        user = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password)
        return f"postgresql://{user}:{password}@{self.postgres_host}:{self.postgres_port}{db_part}"

    def to_sling_connection(self) -> dict[str, Any]:
        """Return a Sling-compatible connection dict for this PostgreSQL."""
        return {
            "type": "postgres",
            "host": self.postgres_host,
            "port": self.postgres_port,
            "database": self.postgres_db,
            "user": self.postgres_user,
            "password": self.postgres_password,
            "schema": getattr(self, "postgres_schema", "public"),
        }


@project_root_cached
def get_settings(project_root: Path) -> PostgresSettings:
    """Return cached PostgreSQL settings for the selected project root.

    Settings are cached per resolved project root, with up to 16 entries, to avoid
    repeated parsing while isolating project configuration; calls for the same root
    return the same instance. Call ``get_settings.cache_clear()`` after changing
    configuration.

    Example:
        >>> settings1 = get_settings()
        >>> settings2 = get_settings()
        >>> settings1 is settings2  # Same cached instance for this root
        True
        >>>
        >>> # Access connection parameters
        >>> settings = get_settings()
        >>> print(f"Connecting to {settings.postgres_host}:{settings.postgres_port}")

    """
    return PostgresSettings()
