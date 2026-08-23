"""Trino catalog plugins for Nessie-backed Iceberg catalogs.

This module provides Trino catalog plugins that configure Iceberg connections
backed by Nessie's REST catalog API. Supports both production and development
Nessie references.

Example:
    >>> from phlo_nessie.adapters.trino import TrinoNessieIcebergCatalogPlugin
    >>> plugin = TrinoNessieIcebergCatalogPlugin()
    >>> props = plugin.get_properties()

Classes:
    TrinoNessieIcebergCatalogPlugin: Main production catalog.
    TrinoNessieIcebergDevCatalogPlugin: Development branch catalog.

"""

from __future__ import annotations

import os

from phlo.plugins.base import CatalogPlugin, PluginMetadata


def _nessie_iceberg_rest_uri() -> str:
    """Build the Nessie Iceberg REST URI from NESSIE_HOST and NESSIE_PORT."""
    host = os.environ.get("NESSIE_HOST", "nessie")
    port = os.environ.get("NESSIE_PORT", "19120")
    return f"http://{host}:{port}/iceberg"


def _base_iceberg_catalog_properties(*, prefix: str | None = None) -> dict[str, str]:
    """Build shared Trino Iceberg catalog properties for a Nessie backend.

    Configures the Iceberg REST connector plus S3/MinIO warehouse storage;
    an optional prefix namespaces the catalog (for example "dev").

    Example:
        >>> props = _base_iceberg_catalog_properties()
        >>> props['iceberg.catalog.type']
        'rest'

    """
    minio_endpoint = os.environ.get("S3_ENDPOINT", "http://minio:9000")
    s3_region = os.environ.get("AWS_REGION", "us-east-1")

    props: dict[str, str] = {
        "connector.name": "iceberg",
        "iceberg.catalog.type": "rest",
        "iceberg.rest-catalog.uri": _nessie_iceberg_rest_uri(),
        "iceberg.rest-catalog.warehouse": "warehouse",
        "fs.native-s3.enabled": "true",
        "s3.endpoint": minio_endpoint,
        "s3.path-style-access": "true",
        "s3.region": s3_region,
    }
    if prefix is not None:
        props["iceberg.rest-catalog.prefix"] = prefix
    return props


class TrinoNessieIcebergCatalogPlugin(CatalogPlugin):
    """Main Trino catalog backed by Nessie Iceberg REST on the default reference.

    Example:
        >>> plugin = TrinoNessieIcebergCatalogPlugin()
        >>> props = plugin.get_properties()
        >>> print(props['iceberg.rest-catalog.uri'])

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for catalog registration."""
        return PluginMetadata(
            name="iceberg",
            version="0.1.0",
            description="Trino Iceberg catalog backed by Nessie REST",
            tags=["trino", "iceberg", "nessie", "lakehouse"],
        )

    @property
    def targets(self) -> list[str]:
        """Return the target systems for this plugin."""
        return ["trino"]

    @property
    def catalog_name(self) -> str:
        """Return the Trino catalog name."""
        return "iceberg"

    def get_properties(self) -> dict[str, str]:
        """Return Trino Iceberg connector configuration properties."""
        return _base_iceberg_catalog_properties()


class TrinoNessieIcebergDevCatalogPlugin(CatalogPlugin):
    """Dev Trino catalog backed by the Nessie dev ref, isolated from production data.

    Example:
        >>> plugin = TrinoNessieIcebergDevCatalogPlugin()
        >>> props = plugin.get_properties()
        >>> print(props['iceberg.rest-catalog.prefix'])
        'dev'

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for catalog registration."""
        return PluginMetadata(
            name="iceberg_dev",
            version="0.1.0",
            description="Trino Iceberg catalog for the Nessie dev ref",
            tags=["trino", "iceberg", "nessie", "dev"],
        )

    @property
    def targets(self) -> list[str]:
        """Return the target systems for this plugin."""
        return ["trino"]

    @property
    def catalog_name(self) -> str:
        """Return the Trino catalog name."""
        return "iceberg_dev"

    def get_properties(self) -> dict[str, str]:
        """Return Trino Iceberg connector properties with the dev prefix."""
        return _base_iceberg_catalog_properties(prefix="dev")
