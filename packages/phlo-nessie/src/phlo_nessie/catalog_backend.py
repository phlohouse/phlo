"""Nessie-owned PyIceberg catalog helpers.

Builds PyIceberg catalog configuration for the Nessie REST catalog with
S3/MinIO storage and loads cached catalog instances per reference.

Example:
    >>> from phlo_nessie.catalog_backend import load_pyiceberg_catalog
    >>> catalog = load_pyiceberg_catalog(ref="main")

"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from phlo.config.network import resolve_url
from phlo.logging import get_logger
from phlo_nessie.settings import get_settings

logger = get_logger(__name__)


def _pyiceberg_catalog_config(ref: str) -> dict[str, Any]:
    """Build the PyIceberg REST-catalog configuration dict connecting to Nessie
    for a given reference.

    Example:
        >>> config = _pyiceberg_catalog_config("main")
        >>> config['type']
        'rest'

    """
    settings = get_settings()
    return {
        "type": "rest",
        "uri": f"{settings.nessie_iceberg_rest_uri()}/{ref}",
        # Nessie REST catalog endpoints expect the configured warehouse identifier,
        # not its physical S3 location.  The local Nessie service names it "warehouse".
        "warehouse": "warehouse",
        "s3.endpoint": resolve_url(
            os.environ.get("ICEBERG_S3_ENDPOINT")
            or os.environ.get("S3_ENDPOINT", "http://minio:10001"),
            port_env_var="MINIO_API_PORT",
        ),
        "s3.access-key-id": os.environ.get("ICEBERG_S3_ACCESS_KEY", "minio"),
        "s3.secret-access-key": os.environ.get("ICEBERG_S3_SECRET_KEY", "minio123"),
        "s3.path-style-access": "true",
        "s3.region": os.environ.get("ICEBERG_S3_REGION", "us-east-1"),
    }


@lru_cache(maxsize=16)
def load_pyiceberg_catalog(ref: str = "main"):
    """Load (and cache) the PyIceberg catalog instance for a Nessie reference.

    Raises RuntimeError when PyIceberg is not installed. At most 16 catalogs
    are cached; least recently used entries are evicted.

    Example:
        >>> catalog = load_pyiceberg_catalog("main")
        >>> tables = catalog.list_tables("raw")

    """
    try:
        from pyiceberg.catalog import load_catalog
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Iceberg catalog support is not installed. Install `phlo-nessie[iceberg-cli]`."
        ) from exc

    logger.debug("nessie_pyiceberg_catalog_load_requested", ref=ref)
    return load_catalog(name=f"iceberg_{ref}", **_pyiceberg_catalog_config(ref))
