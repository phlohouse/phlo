"""ClickHouse settings configuration.

This module provides Pydantic-based configuration management for ClickHouse
connection parameters, including host, port, authentication, and security settings.

Example:
    Loading ClickHouse settings:

    >>> from phlo_clickhouse.settings import get_settings, ClickHouseSettings
    >>> settings = get_settings()
    >>> settings.clickhouse_host
    'clickhouse'


    Service settings for ClickHouse, built on the shared phlo.config base/cache/network helpers.
    Loaded within phlo_clickhouse by plugin and resource code through get_settings().
"""

from __future__ import annotations

from typing import Any

from pathlib import Path

from pydantic import Field

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached
from phlo.config.network import resolve_host


class ClickHouseSettings(BaseConfig):
    """ClickHouse data plane configuration model: connection parameters for
    host, ports, authentication, database, and TLS.

    Example:
        >>> settings = ClickHouseSettings(
        ...     clickhouse_host="localhost",
        ...     clickhouse_db="analytics"
        ... )
        >>> settings.clickhouse_http_endpoint()
        'localhost:8123'

    """

    clickhouse_host: str = Field(default="clickhouse", description="ClickHouse service hostname")
    clickhouse_http_port: int = Field(default=8123, description="ClickHouse HTTP interface port")
    clickhouse_native_port: int = Field(
        default=19000, description="ClickHouse native protocol port"
    )
    clickhouse_user: str = Field(default="default", description="ClickHouse username")
    clickhouse_password: str = Field(default="", description="ClickHouse password")
    clickhouse_db: str = Field(default="default", description="Default ClickHouse database")
    clickhouse_secure: bool = Field(default=False, description="Use TLS for ClickHouse connections")

    def model_post_init(self, __context: object) -> None:
        host, port = resolve_host(
            self.clickhouse_host,
            self.clickhouse_http_port,
            port_env_var="CLICKHOUSE_HTTP_PORT",
        )
        object.__setattr__(self, "clickhouse_host", host)
        object.__setattr__(self, "clickhouse_http_port", port)

    def clickhouse_http_endpoint(self) -> str:
        """Return the "host:port" endpoint for the HTTP interface.

        Example:
            >>> settings = ClickHouseSettings(clickhouse_host="localhost", clickhouse_http_port=8123)
            >>> settings.clickhouse_http_endpoint()
            'localhost:8123'

        """
        return f"{self.clickhouse_host}:{self.clickhouse_http_port}"

    def clickhouse_native_endpoint(self) -> str:
        """Return the "host:port" endpoint for the native protocol interface.

        Example:
            >>> settings = ClickHouseSettings(clickhouse_host="localhost", clickhouse_native_port=9000)
            >>> settings.clickhouse_native_endpoint()
            'localhost:9000'

        """
        return f"{self.clickhouse_host}:{self.clickhouse_native_port}"

    def to_sling_connection(self) -> dict[str, Any]:
        """Return a Sling-compatible native-protocol ClickHouse connection dict."""
        return {
            "type": "clickhouse",
            "host": self.clickhouse_host,
            "port": self.clickhouse_native_port,
            "database": self.clickhouse_db,
            "user": self.clickhouse_user,
            "password": self.clickhouse_password,
        }


@project_root_cached
def get_settings(project_root: Path) -> ClickHouseSettings:
    """Return the ClickHouseSettings instance cached per project root.

    Example:
        >>> settings = get_settings()
        >>> settings.clickhouse_host
        'clickhouse'
        >>> # Subsequent calls for the same root return the same cached instance
        >>> get_settings() is settings
        True

    """
    return ClickHouseSettings()
