"""Phlo MinIO package for S3-compatible object storage.

This package provides a complete MinIO integration for Phlo, offering
S3-compatible object storage capabilities for data lake operations.

Examples:
    Basic usage with settings:
        >>> from phlo_minio import get_settings
        >>> settings = get_settings()
        >>> print(settings.minio_endpoint())
        'minio:10001'

    Using the service plugin:
        >>> from phlo_minio import MinioServicePlugin
        >>> plugin = MinioServicePlugin()
        >>> print(plugin.metadata.name)
        'minio'

MinIO runs on ports 10001 (API) and 10002 (Console) by default;
default credentials: minio / minio123.
"""

from phlo_minio.plugin import MinioServicePlugin
from phlo_minio.settings import MinioSettings, get_settings

__all__ = ["MinioServicePlugin", "MinioSettings", "get_settings"]
from importlib.metadata import version

__version__ = version("phlo-minio")
