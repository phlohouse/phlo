"""RustFS settings.

This module defines the configuration schema and defaults for connecting to
RustFS (S3-compatible object storage). Settings are loaded from environment
variables and validated using Pydantic.

Classes:
    RustfsSettings: Pydantic configuration model for RustFS connectivity.

Functions:
    get_settings: Returns a cached RustfsSettings instance.

    Settings for the RustFS object store, built on the shared phlo.config base/cache/network helpers.
    Loaded within phlo_rustfs by plugin code through get_settings().
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached
from phlo.config.network import resolve_host


class RustfsSettings(BaseConfig):
    """RustFS S3-compatible storage configuration.

    Pydantic model loaded from environment variables with local-development
    defaults: rustfs_host, rustfs_access_key/rustfs_secret_key,
    rustfs_api_port (9000), rustfs_console_port (9001), and s3_region
    ("us-east-1").

    Example:
        >>> settings = RustfsSettings()
        >>> print(settings.rustfs_endpoint())
        "localhost:9000"

    """

    rustfs_host: str = Field(default="rustfs", description="RustFS service hostname")
    rustfs_access_key: str = Field(default="rustfsadmin", description="RustFS access key")
    rustfs_secret_key: str = Field(default="rustfsadmin", description="RustFS secret key")
    rustfs_api_port: int = Field(default=9000, description="RustFS S3 API port")
    rustfs_console_port: int = Field(default=9001, description="RustFS console port")
    s3_region: str = Field(default="us-east-1", description="S3 region")

    def model_post_init(self, __context: Any) -> None:
        """Resolve host and port after initialization via resolve_host,
        honoring RUSTFS_API_PORT; object.__setattr__ bypasses Pydantic's
        frozen post-init protections. __context is Pydantic-internal.
        """
        host, port = resolve_host(
            self.rustfs_host, self.rustfs_api_port, port_env_var="RUSTFS_API_PORT"
        )
        object.__setattr__(self, "rustfs_host", host)
        object.__setattr__(self, "rustfs_api_port", port)

    def rustfs_endpoint(self) -> str:
        """Return the resolved "host:port" endpoint for the RustFS S3 API.

        Example:
            >>> settings = RustfsSettings()
            >>> settings.rustfs_endpoint()
            "localhost:9000"

        """
        return f"{self.rustfs_host}:{self.rustfs_api_port}"


@project_root_cached
def get_settings(project_root: Path) -> RustfsSettings:
    """Return cached RustFS settings for the selected project root (cache
    holds up to 16 entries per root).

    Example:
        >>> settings = get_settings()
        >>> same_settings = get_settings()
        >>> settings is same_settings
        True

    """
    return RustfsSettings()
