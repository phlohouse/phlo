"""Iceberg settings configuration.

This module provides configuration management for Iceberg connections,
including warehouse paths, S3 storage settings, and Nessie catalog endpoints.

Settings are loaded from environment variables and ``.phlo/.env`` files,
following Phlo's standard configuration pattern.

Configuration precedence:
    1. Environment variables (``PHLO_ICEBERG_*``)
    2. ``.phlo/.env.local`` (local overrides)
    3. ``.phlo/.env`` (project defaults)
    4. Default values defined in this module

Example:
    Basic settings usage::

        from phlo_iceberg.settings import get_settings

        settings = get_settings()
        print(f"Warehouse: {settings.iceberg_warehouse_path}")
        print(f"Default branch: {settings.iceberg_default_ref}")

        # Get catalog config for PyIceberg
        catalog_config = settings.get_pyiceberg_catalog_config(ref="main")

    Environment variables::

        export PHLO_ICEBERG_WAREHOUSE_PATH=s3://my-bucket/warehouse
        export PHLO_ICEBERG_DEFAULT_REF=main
        export PHLO_ICEBERG_S3_ACCESS_KEY=mykey
        export PHLO_ICEBERG_S3_SECRET_KEY=mysecret

Package configuration boundary, building on phlo.config.base, phlo.config.cache,
and phlo.config.network; consumed through get_settings() rather than imported widely.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached
from phlo.config.network import resolve_url as _resolve_service_url
from phlo_iceberg.compatibility import validate_pyiceberg_rest_catalog_config


class IcebergSettings(BaseConfig):
    """Iceberg catalog and storage configuration for connecting via the
    Nessie REST catalog and S3-compatible storage (MinIO).

    Defaults: warehouse path ``s3://lake/warehouse``, staging path
    ``s3://lake/stage``, default namespace ``raw``, default ref ``main``,
    S3 endpoint ``http://minio:10001`` with access key ``minio`` / secret key
    ``minio123`` in region ``us-east-1``, and catalog URI
    ``http://nessie:19120/iceberg``. Each value can be overridden through
    environment variables (``PHLO_ICEBERG_*`` aliases, plus standard AWS
    variables for S3 credentials and region).

    Example:
        Configure via environment::

            export PHLO_ICEBERG_WAREHOUSE_PATH=s3://production/warehouse
            export PHLO_ICEBERG_CATALOG_URI=http://nessie.prod:19120/iceberg
            export PHLO_ICEBERG_S3_ACCESS_KEY=prod-key
            export PHLO_ICEBERG_S3_SECRET_KEY=prod-secret

        Access in code::

            from phlo_iceberg.settings import get_settings

            settings = get_settings()
            config = settings.get_pyiceberg_catalog_config(ref="main")
            catalog = load_catalog(**config)

    """

    iceberg_warehouse_path: str = Field(
        default="s3://lake/warehouse",
        validation_alias=AliasChoices(
            "iceberg_warehouse_path", "PHLO_ICEBERG_WAREHOUSE_PATH", "ICEBERG_WAREHOUSE_PATH"
        ),
        description="S3 path for Iceberg warehouse",
    )
    iceberg_staging_path: str = Field(
        default="s3://lake/stage", description="S3 path for staging parquet files"
    )
    iceberg_default_namespace: str = Field(
        default="raw", description="Default namespace/schema for Iceberg tables"
    )
    iceberg_default_ref: str = Field(
        default="main", description="Default catalog ref/branch for Iceberg operations"
    )
    iceberg_s3_endpoint: str = Field(
        default="http://minio:10001",
        validation_alias=AliasChoices(
            "iceberg_s3_endpoint", "PHLO_ICEBERG_S3_ENDPOINT", "ICEBERG_S3_ENDPOINT"
        ),
        description="S3 endpoint URL for Iceberg I/O",
    )
    iceberg_s3_access_key: str = Field(
        default="minio",
        validation_alias=AliasChoices(
            "iceberg_s3_access_key",
            "PHLO_ICEBERG_S3_ACCESS_KEY",
            "ICEBERG_S3_ACCESS_KEY",
            "AWS_ACCESS_KEY_ID",
        ),
        description="S3 access key for Iceberg I/O",
    )
    iceberg_s3_secret_key: str = Field(
        default="minio123",
        validation_alias=AliasChoices(
            "iceberg_s3_secret_key",
            "PHLO_ICEBERG_S3_SECRET_KEY",
            "ICEBERG_S3_SECRET_KEY",
            "AWS_SECRET_ACCESS_KEY",
        ),
        description="S3 secret key for Iceberg I/O",
    )
    iceberg_s3_region: str = Field(
        default="us-east-1",
        validation_alias=AliasChoices(
            "iceberg_s3_region",
            "PHLO_ICEBERG_S3_REGION",
            "ICEBERG_S3_REGION",
            "AWS_REGION",
            "AWS_DEFAULT_REGION",
        ),
        description="S3 region for Iceberg I/O",
    )
    iceberg_catalog_uri: str = Field(
        default="http://nessie:19120/iceberg",
        validation_alias=AliasChoices(
            "iceberg_catalog_uri", "PHLO_ICEBERG_CATALOG_URI", "ICEBERG_CATALOG_URI"
        ),
        description="Iceberg REST catalog endpoint base URI",
    )

    def get_iceberg_warehouse_for_branch(self, branch: str = "main") -> str:
        """Return the warehouse path for a Nessie branch.

        All branches currently share the same warehouse path; future versions may
        support branch-specific locations.

        Example:
            Get warehouse path::

                settings = get_settings()
                path = settings.get_iceberg_warehouse_for_branch("main")
                print(f"Warehouse: {path}")  # s3://lake/warehouse

        """
        return self.iceberg_warehouse_path

    def get_pyiceberg_catalog_config(self, ref: str = "main") -> dict:
        """Build PyIceberg REST catalog configuration suitable for passing to
        ``pyiceberg.catalog.load_catalog()``.

        Resolves service URLs dynamically based on environment configuration and
        returns a dict with keys ``type`` (always "rest"), ``uri`` (full catalog URI
        including the ``ref`` path), ``warehouse`` (the Nessie warehouse identifier),
        ``s3.endpoint``, ``s3.access-key-id``, ``s3.secret-access-key``,
        ``s3.path-style-access`` (always "true" for MinIO compatibility), and
        ``s3.region``.

        Example:
            Configure PyIceberg catalog::

                from pyiceberg.catalog import load_catalog
                from phlo_iceberg.settings import get_settings

                settings = get_settings()
                config = settings.get_pyiceberg_catalog_config(ref="dev")
                catalog = load_catalog("dev_catalog", **config)

                # Now use catalog
                tables = catalog.list_tables("raw")

        """
        catalog_uri = _resolve_service_url(self.iceberg_catalog_uri, port_env_var="NESSIE_PORT")
        s3_endpoint = _resolve_service_url(self.iceberg_s3_endpoint, port_env_var="MINIO_API_PORT")
        config = {
            "type": "rest",
            "uri": f"{catalog_uri}/{ref}",
            # Nessie's REST catalog uses its configured warehouse identifier here;
            # the physical S3 location remains iceberg_warehouse_path for storage.
            "warehouse": "warehouse",
            "s3.endpoint": s3_endpoint,
            "s3.access-key-id": self.iceberg_s3_access_key,
            "s3.secret-access-key": self.iceberg_s3_secret_key,
            "s3.path-style-access": "true",
            "s3.region": self.iceberg_s3_region,
        }
        validate_pyiceberg_rest_catalog_config(config)
        return config


@project_root_cached
def get_settings(project_root: Path) -> IcebergSettings:
    """Get cached Iceberg settings for the selected project root.

    Settings are cached per resolved project root, with up to 16 entries, to avoid
    repeatedly loading and parsing configuration; calls for the same root return the
    same instance. Call ``get_settings.cache_clear()`` to force a reload after
    configuration changes.

    Example:
        Get settings::

            from phlo_iceberg.settings import get_settings

            settings = get_settings()
            print(f"Warehouse: {settings.iceberg_warehouse_path}")
            print(f"Default namespace: {settings.iceberg_default_namespace}")

    """
    return IcebergSettings()
