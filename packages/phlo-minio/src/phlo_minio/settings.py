"""MinIO settings module for S3-compatible storage configuration.

This module provides configuration management for MinIO connections,
including host resolution, port configuration, and S3-compatible settings.

Examples:
    Get cached settings instance:
        >>> from phlo_minio.settings import get_settings
        >>> settings = get_settings()
        >>> endpoint = settings.minio_endpoint()

    Create custom settings:
        >>> from phlo_minio.settings import MinioSettings
        >>> settings = MinioSettings(
        ...     minio_host="custom-minio",
        ...     minio_api_port=9000,
        ...     minio_root_user="admin",
        ...     minio_root_password="secretpass"
        ... )


Package-local settings module built on the shared phlo.config base and caching machinery.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached
from phlo.config.network import resolve_host


class MinioSettings(BaseConfig):
    """Configuration for MinIO S3-compatible storage.

    Covers host, credentials, ports, and S3 region. ``model_post_init``
    resolves the host and API port from environment variables when
    available, enabling Docker Compose service discovery and local
    development overrides.

    Examples:
        Default configuration:
            >>> settings = MinioSettings()
            >>> print(settings.minio_host)
            'minio'
            >>> print(settings.s3_region)
            'us-east-1'

        Custom endpoint configuration:
            >>> settings = MinioSettings(minio_host="localhost", minio_api_port=9000)
            >>> print(settings.minio_endpoint())
            'localhost:9000'

        Environment-based resolution:
            # With MINIO_API_PORT=9001 in environment:
            >>> settings = MinioSettings()
            >>> print(settings.minio_api_port)  # Resolved from env
            9001
    """

    minio_host: str = Field(default="minio", description="MinIO service hostname")
    minio_root_user: str = Field(default="minio", description="MinIO root username")
    minio_root_password: str = Field(default="minio123", description="MinIO root password")
    minio_api_port: int = Field(default=10001, description="MinIO API port")
    minio_console_port: int = Field(default=10002, description="MinIO console port")
    s3_region: str = Field(default="us-east-1", description="S3 region")

    def model_post_init(self, __context: Any) -> None:
        """Resolve host and port from environment variables if available.

        Updates ``minio_host`` and ``minio_api_port`` via
        ``phlo.config.network.resolve_host`` so Docker Compose service
        discovery and local development overrides work without code
        changes. ``__context`` is Pydantic's unused internal context.

        Examples:
            Automatic host resolution:
                # With MINIO_HOST=localhost in environment
                >>> settings = MinioSettings()
                >>> settings.minio_host  # Resolved to 'localhost'
                'localhost'
        """
        host, port = resolve_host(
            self.minio_host, self.minio_api_port, port_env_var="MINIO_API_PORT"
        )
        object.__setattr__(self, "minio_host", host)
        object.__setattr__(self, "minio_api_port", port)

    def minio_endpoint(self) -> str:
        """Return the MinIO API endpoint as host:port string.

        Suitable for S3 client configuration such as ``endpoint_url``.

        Examples:
            Default endpoint:
                >>> settings = MinioSettings()
                >>> settings.minio_endpoint()
                'minio:10001'

            Custom endpoint:
                >>> settings = MinioSettings(minio_host="localhost", minio_api_port=9000)
                >>> settings.minio_endpoint()
                'localhost:9000'

            Use this endpoint for S3 client configuration:
                >>> import boto3
                >>> s3 = boto3.client(
                ...     's3',
                ...     endpoint_url=f"http://{settings.minio_endpoint()}",
                ...     aws_access_key_id=settings.minio_root_user,
                ...     aws_secret_access_key=settings.minio_root_password
                ... )
        """
        return f"{self.minio_host}:{self.minio_api_port}"


@project_root_cached
def get_settings(project_root: Path) -> MinioSettings:
    """Return cached MinIO settings for the selected project root.

    Settings are cached per resolved project root (up to 16 entries),
    avoiding repeated environment resolution and configuration loading;
    call ``get_settings.cache_clear()`` to refresh after configuration
    changes.

    Examples:
        Same-root cache reuse:
            >>> settings1 = get_settings()
            >>> settings2 = get_settings()
            >>> settings1 is settings2  # Same instance
            True

        Accessing configuration:
            >>> settings = get_settings()
            >>> endpoint = settings.minio_endpoint()
            >>> print(f"MinIO at {endpoint}")
            MinIO at minio:10001

        Integration with S3 clients:
            >>> settings = get_settings()
            >>> s3_config = {
            ...     'endpoint_url': f"http://{settings.minio_endpoint()}",
            ...     'aws_access_key_id': settings.minio_root_user,
            ...     'aws_secret_access_key': settings.minio_root_password,
            ...     'region_name': settings.s3_region
            ... }
    """
    return MinioSettings()
