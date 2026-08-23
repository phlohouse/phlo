"""Trino settings and configuration management.

This module provides configuration management for Trino connections,
including host resolution, port configuration, and DSN generation.

Functions:
    build_trino_dsn: Build a Trino DSN string from components.
    get_settings: Return cached Trino settings instance.

Classes:
    TrinoSettings: Configuration model for Trino query engine.

Example:
    >>> from phlo_trino.settings import TrinoSettings, get_settings
    >>> settings = get_settings()
    >>> dsn = settings.trino_connection_string()
    >>> print(dsn)
    trino://trino:10005/iceberg

Part of the phlo-trino package's configuration layer, built on the shared phlo.config base,
cache, and network host-resolution helpers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached
from phlo.config.network import resolve_host


def build_trino_dsn(host: str, port: int, catalog: str) -> str:
    """Build a Trino DSN string in the form ``trino://<host>:<port>/<catalog>``."""
    return f"trino://{host}:{port}/{catalog}"


class TrinoSettings(BaseConfig):
    """Trino query engine configuration."""

    trino_version: str = Field(default="477", description="Trino version")
    trino_port: int = Field(default=10005, description="Trino HTTP port")
    trino_host: str = Field(default="trino", description="Trino service hostname")
    trino_catalog: str = Field(default="iceberg", description="Trino catalog name for Iceberg")
    trino_default_ref: str = Field(
        default="main",
        description="Default branch/tag suffix",
    )

    def model_post_init(self, __context: Any) -> None:
        """Post-initialization hook to resolve host and port."""
        host, port = resolve_host(self.trino_host, self.trino_port, port_env_var="TRINO_PORT")
        object.__setattr__(self, "trino_host", host)
        object.__setattr__(self, "trino_port", port)

    def trino_connection_string(self) -> str:
        """Return the Trino DSN for current settings."""
        return build_trino_dsn(self.trino_host, self.trino_port, self.trino_catalog)


@project_root_cached
def get_settings(project_root: Path) -> TrinoSettings:
    """Return cached Trino settings."""
    return TrinoSettings()
