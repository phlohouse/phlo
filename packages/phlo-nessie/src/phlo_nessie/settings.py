"""Nessie configuration settings module.

This module provides Pydantic-based configuration management for the Nessie
catalog service, including host resolution, port configuration, and URI builders
for various Nessie API endpoints.

Example:
    >>> from phlo_nessie.settings import get_settings
    >>> settings = get_settings()
    >>> print(settings.nessie_uri())
    'http://nessie:19120/api'

Part of the phlo-nessie package's configuration layer, built on the shared phlo.config base,
cache, and network host-resolution helpers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached
from phlo.config.network import resolve_host


class NessieSettings(BaseConfig):
    """Nessie catalog configuration."""

    nessie_version: str = Field(default="0.108.3", description="Nessie version")
    nessie_port: int = Field(default=19120, description="Nessie REST API port")
    nessie_host: str = Field(default="nessie", description="Nessie service hostname")
    nessie_api_version: str = Field(default="v1", description="Nessie API version")
    nessie_default_ref: str = Field(
        default="main",
        description="Default Nessie branch/tag",
    )
    nessie_query_engine: str | None = Field(
        default=None,
        description="Optional query_engine capability name for catalog scan fallbacks",
    )

    def model_post_init(self, __context: Any) -> None:
        """Post-initialization hook to resolve host and port."""
        host, port = resolve_host(self.nessie_host, self.nessie_port, port_env_var="NESSIE_PORT")
        # object.__setattr__ skips pydantic's validated assignment, which is
        # fine here: the resolved values keep the declared types.
        object.__setattr__(self, "nessie_host", host)
        object.__setattr__(self, "nessie_port", port)

    def nessie_uri(self) -> str:
        """Return the base Nessie API URI."""
        return f"http://{self.nessie_host}:{self.nessie_port}/api"

    def nessie_api_uri(self) -> str:
        """Return the versioned Nessie API URI."""
        return f"http://{self.nessie_host}:{self.nessie_port}/api/{self.nessie_api_version}"

    def nessie_iceberg_rest_uri(self) -> str:
        """Return the Nessie Iceberg REST catalog URI."""
        return f"http://{self.nessie_host}:{self.nessie_port}/iceberg"


@project_root_cached
def get_settings(project_root: Path) -> NessieSettings:
    """Return cached Nessie settings for the selected project root."""
    return NessieSettings()
