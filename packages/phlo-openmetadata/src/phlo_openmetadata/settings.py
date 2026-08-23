"""OpenMetadata settings configuration.

Provides Pydantic-based configuration management for OpenMetadata integration,
including server connection settings, authentication credentials, and sync options.

Example:
    >>> from phlo_openmetadata.settings import OpenMetadataSettings, get_settings
    >>> settings = get_settings()
    >>> settings.openmetadata_uri()
    'http://openmetadata-server:8585/api'

Builds on phlo.config.* and resolves query-engine mappings via phlo_openmetadata.capabilities.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached
from phlo.config.network import resolve_host
from phlo_openmetadata.capabilities import (
    resolve_query_engine_catalog,
    resolve_query_engine_service_type,
)


class OpenMetadataSettings(BaseConfig):
    """OpenMetadata integration configuration.

    Covers connection parameters, authentication credentials, and sync
    behavior for the OpenMetadata integration.

    Example:
        >>> settings = OpenMetadataSettings()
        >>> settings.openmetadata_uri()
        'http://openmetadata-server:8585/api'

    """

    openmetadata_host: str = Field(
        default="openmetadata-server", description="OpenMetadata server hostname"
    )
    openmetadata_port: int = Field(default=8585, description="OpenMetadata server port")
    openmetadata_username: str = Field(default="admin", description="OpenMetadata admin username")
    openmetadata_password: str = Field(default="admin", description="OpenMetadata admin password")
    openmetadata_verify_ssl: bool = Field(
        default=False, description="Verify SSL certificates for OpenMetadata connections"
    )
    openmetadata_service_name: str = Field(
        default="phlo",
        description="OpenMetadata database service name for Phlo metadata sync",
    )
    openmetadata_service_type: str | None = Field(
        default=None,
        description="OpenMetadata database service type (required unless query_engine metadata declares service_type)",
    )
    openmetadata_catalog_scanner: str | None = Field(
        default=None,
        description="Catalog scanner capability name to use for sync operations",
    )
    openmetadata_query_engine: str | None = Field(
        default=None,
        description="Query engine capability name used to infer the OpenMetadata database name",
    )
    openmetadata_database_name: str | None = Field(
        default=None,
        description="OpenMetadata database name (required unless a query_engine capability declares catalog metadata)",
    )
    openmetadata_dbt_manifest_path: str = Field(
        default="workflows/transforms/dbt/target/manifest.json",
        description="Path to dbt manifest.json for metadata sync",
    )
    openmetadata_dbt_catalog_path: str = Field(
        default="workflows/transforms/dbt/target/catalog.json",
        description="Path to dbt catalog.json for metadata sync",
    )
    openmetadata_sync_enabled: bool = Field(
        default=True, description="Enable automatic metadata sync to OpenMetadata"
    )
    openmetadata_sync_interval_seconds: int = Field(
        default=300, description="Minimum interval between metadata syncs (seconds)"
    )

    def model_post_init(self, __context: object) -> None:
        """Resolve final host and port from configuration and environment overrides."""
        host, port = resolve_host(
            self.openmetadata_host,
            self.openmetadata_port,
            port_env_var="OPENMETADATA_PORT",
        )
        object.__setattr__(self, "openmetadata_host", host)
        object.__setattr__(self, "openmetadata_port", port)

    def openmetadata_uri(self) -> str:
        """Build the OpenMetadata API base URI."""
        return f"http://{self.openmetadata_host}:{self.openmetadata_port}/api"

    def openmetadata_database(self) -> str:
        """Resolve the OpenMetadata database name.

        Prefers explicit configuration, then the query engine capability;
        raises RuntimeError when neither resolves.
        """
        if self.openmetadata_database_name:
            return self.openmetadata_database_name
        return resolve_query_engine_catalog(self.openmetadata_query_engine)

    def openmetadata_database_service_type(self) -> str:
        """Resolve the OpenMetadata service type (e.g., 'Trino', 'Snowflake').

        Prefers explicit configuration, then the query engine capability;
        raises RuntimeError when neither resolves.
        """
        if self.openmetadata_service_type:
            return self.openmetadata_service_type
        return resolve_query_engine_service_type(self.openmetadata_query_engine)


@project_root_cached
def get_settings(project_root: Path) -> OpenMetadataSettings:
    """Get cached OpenMetadata settings for the selected project root."""
    return OpenMetadataSettings()
