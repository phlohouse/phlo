"""PyIceberg REST catalog configuration for Polaris.

Both dlt ingestion and snapshot promotion configure PyIceberg identically:
the writer principal authenticates against Polaris's OAuth2 token endpoint
and reads S3 credentials from the environment (production deployments should
prefer Polaris credential vending over static keys).
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from phlo_polaris.settings import get_settings


def current_snapshot_id(table: Any) -> int | None:
    """Return the table's current snapshot id.

    PyIceberg 0.11 removed ``Table.current_snapshot_id()``; the supported
    path is ``current_snapshot().snapshot_id``.
    """
    snapshot = table.current_snapshot()
    return snapshot.snapshot_id if snapshot is not None else None


def _writer_credential() -> str:
    """Use the same persisted principal credential as the Trino adapter."""
    return get_settings().writer_credential()


def _pyiceberg_catalog_config() -> dict[str, Any]:
    settings = get_settings()
    return {
        "type": "rest",
        "warehouse": settings.polaris_catalog,
        "uri": settings.polaris_rest_catalog_uri(),
        "credential": _writer_credential(),
        "scope": "PRINCIPAL_ROLE:ALL",
        "oauth2-server-uri": settings.oauth_token_uri(),
        "s3.endpoint": os.environ.get("ICEBERG_S3_ENDPOINT", "http://minio:9000/"),
        "s3.access-key-id": os.environ.get("ICEBERG_S3_ACCESS_KEY", "minio"),
        "s3.secret-access-key": os.environ.get("ICEBERG_S3_SECRET_KEY")
        or os.environ.get("MINIO_ROOT_PASSWORD", ""),
        "s3.path-style-access": "true",
        "s3.region": os.environ.get("ICEBERG_S3_REGION", "us-east-1"),
    }


@lru_cache(maxsize=4)
def load_pyiceberg_catalog():
    """Load the Polaris-backed PyIceberg REST catalog."""
    from pyiceberg.catalog import load_catalog

    config = _pyiceberg_catalog_config()
    return load_catalog(name=f"polaris_{config['warehouse']}", **config)
