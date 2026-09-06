"""Trino catalog plugin for the Polaris-backed Iceberg REST catalog.

Configures Trino's Iceberg REST connector against Polaris with OAuth2
principal credentials and credential vending enabled. Polaris and Nessie
both register the ``iceberg`` catalog name by design: a project selects one
catalog per warehouse (``catalog: nessie`` or ``catalog: polaris``) and
installs exactly one of the two packages.
"""

from __future__ import annotations

import os

from phlo.plugins.base import CatalogPlugin, PluginMetadata


def _polaris_iceberg_rest_uri() -> str:
    """Build the Polaris Iceberg REST catalog URI from the environment."""
    host = os.environ.get("POLARIS_HOST", "polaris")
    port = os.environ.get("POLARIS_PORT", "8181")
    return f"http://{host}:{port}/api/catalog"


def base_iceberg_catalog_properties() -> dict[str, str]:
    """Build Trino Iceberg catalog properties for a Polaris backend.

    Uses the writer principal over OAuth2 with vended credentials so Trino
    never sees static S3 keys directly.
    """
    minio_endpoint = os.environ.get("S3_ENDPOINT", "http://minio:9000")
    s3_region = os.environ.get("AWS_REGION", "us-east-1")
    client_id = os.environ.get("POLARIS_WRITER_CLIENT_ID", "phlo_writer")
    client_secret = os.environ.get("POLARIS_WRITER_CLIENT_SECRET", "phlo-writer-secret")
    warehouse = os.environ.get("POLARIS_CATALOG", "phlo")

    return {
        "connector.name": "iceberg",
        "iceberg.catalog.type": "rest",
        "iceberg.rest-catalog.uri": _polaris_iceberg_rest_uri(),
        "iceberg.rest-catalog.warehouse": warehouse,
        "iceberg.rest-catalog.security.type": "OAUTH2",
        "iceberg.rest-catalog.oauth2.credential": f"{client_id}:{client_secret}",
        "iceberg.rest-catalog.vended-credentials-enabled": "true",
        "fs.native-s3.enabled": "true",
        "s3.endpoint": minio_endpoint,
        "s3.path-style-access": "true",
        "s3.region": s3_region,
    }


class TrinoPolarisIcebergCatalogPlugin(CatalogPlugin):
    """Trino Iceberg catalog backed by Apache Polaris REST with OAuth2."""

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for catalog registration."""
        return PluginMetadata(
            name="iceberg",
            version="0.1.0",
            description="Trino Iceberg catalog backed by Apache Polaris REST",
            tags=["trino", "iceberg", "polaris", "lakehouse"],
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
        return base_iceberg_catalog_properties()
