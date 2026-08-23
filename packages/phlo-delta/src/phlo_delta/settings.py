"""Delta Lake settings and configuration management.

This module provides configuration management for Delta Lake storage,
including S3 endpoints, credentials, warehouse paths, and storage options.

Example:
    from phlo_delta.settings import get_settings

    settings = get_settings()
    storage_opts = settings.get_storage_options()

Builds on phlo.config.base, phlo.config.cache, and phlo.config.network URL resolution.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached
from phlo.config.network import resolve_url


class DeltaSettings(BaseConfig):
    """Delta Lake storage configuration.

    Manages S3 storage paths, endpoints, credentials, and behavior flags for
    Delta Lake operations.

    Example:
        settings = DeltaSettings()
        uri = settings.delta_warehouse_path
        opts = settings.get_storage_options()

    """

    delta_warehouse_path: str = Field(
        default="s3://lake/warehouse/delta", description="S3 path for Delta tables"
    )
    delta_staging_path: str = Field(
        default="s3://lake/stage", description="S3 path for staging parquet files"
    )
    delta_default_namespace: str = Field(
        default="raw", description="Default namespace/schema for Delta tables"
    )
    delta_s3_endpoint: str | None = Field(
        default="http://localhost:9000",
        validation_alias=AliasChoices("DELTA_S3_ENDPOINT", "AWS_S3_ENDPOINT"),
        description="S3 endpoint URL for Delta I/O",
    )
    delta_s3_access_key: str = Field(
        default="minio",
        validation_alias=AliasChoices("DELTA_S3_ACCESS_KEY", "AWS_ACCESS_KEY_ID"),
        description="S3 access key for Delta I/O",
    )
    delta_s3_secret_key: str = Field(
        default="minio123",
        validation_alias=AliasChoices("DELTA_S3_SECRET_KEY", "AWS_SECRET_ACCESS_KEY"),
        description="S3 secret key for Delta I/O",
    )
    delta_s3_region: str = Field(
        default="us-east-1",
        validation_alias=AliasChoices("DELTA_S3_REGION", "AWS_REGION"),
        description="S3 region for Delta I/O",
    )
    delta_s3_allow_unsafe_rename: bool = Field(
        default=True,
        description="Allow unsafe rename for S3 (non-HDFS) backends",
    )

    def model_post_init(self, __context: Any) -> None:
        """Post-initialization hook to resolve S3 endpoint URL.

        Resolves delta_s3_endpoint with the network URL resolver, handling
        port environment variable substitution.
        """
        if self.delta_s3_endpoint:
            resolved = resolve_url(self.delta_s3_endpoint, port_env_var="MINIO_API_PORT")
            object.__setattr__(self, "delta_s3_endpoint", resolved)

    def get_storage_options(self) -> dict[str, str]:
        """Build deltalake storage options dict for S3 access.

        Constructs a dictionary of storage options compatible with the
        deltalake library's S3 I/O operations, containing AWS credentials,
        endpoint URL, region, and safety flags.

        Example:
            settings = DeltaSettings()
            opts = settings.get_storage_options()
            # Returns: {"AWS_ACCESS_KEY_ID": "...", ...}

        """
        opts: dict[str, str] = {
            "AWS_ACCESS_KEY_ID": self.delta_s3_access_key,
            "AWS_SECRET_ACCESS_KEY": self.delta_s3_secret_key,
            "AWS_REGION": self.delta_s3_region,
            "AWS_ALLOW_HTTP": "true",
        }
        if self.delta_s3_endpoint:
            opts["AWS_ENDPOINT_URL"] = self.delta_s3_endpoint
        if self.delta_s3_allow_unsafe_rename:
            opts["AWS_S3_ALLOW_UNSAFE_RENAME"] = "true"
        return opts


@project_root_cached
def get_settings(project_root: Path) -> DeltaSettings:
    """Get cached Delta Lake settings for the selected project root.

    Settings are cached per resolved project root, with up to 16 entries,
    and reused across the application lifecycle.

    Example:
        settings = get_settings()
        path = settings.delta_warehouse_path

    """
    return DeltaSettings()
