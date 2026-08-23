"""Tests validation of pyiceberg REST catalog configuration against the
declared Apache Iceberg compatibility target."""

from __future__ import annotations

import pytest

from phlo_iceberg.compatibility import (
    APACHE_ICEBERG_COMPATIBILITY_TARGET,
    validate_pyiceberg_rest_catalog_config,
)
from phlo_iceberg.plugin import IcebergResourceProvider


def test_pyiceberg_rest_catalog_config_is_compatible_with_iceberg_1_11() -> None:
    config = {
        "type": "rest",
        "uri": "http://nessie:19120/iceberg/main",
        "warehouse": "s3://lake/warehouse",
        "s3.endpoint": "http://minio:10001",
        "s3.access-key-id": "minio",
        "s3.secret-access-key": "minio123",
        "s3.path-style-access": "true",
        "s3.region": "us-east-1",
    }

    result = validate_pyiceberg_rest_catalog_config(config)

    assert APACHE_ICEBERG_COMPATIBILITY_TARGET == "1.11"
    assert result["compatible"] is True
    assert result["target"] == "apache-iceberg-1.11"
    assert result["checks"] == [
        "rest-catalog-type",
        "nessie-iceberg-rest-uri",
        "pyiceberg-ref-in-uri",
        "warehouse-configured",
        "s3-path-style-access",
    ]


def test_pyiceberg_rest_catalog_config_rejects_trino_prefix_property() -> None:
    config = {
        "type": "rest",
        "uri": "http://nessie:19120/iceberg/main",
        "warehouse": "s3://lake/warehouse",
        "prefix": "main",
        "s3.path-style-access": "true",
    }

    with pytest.raises(ValueError, match="not prefix properties"):
        validate_pyiceberg_rest_catalog_config(config)


def test_iceberg_table_store_advertises_compatibility_as_capability_metadata() -> None:
    table_store = IcebergResourceProvider().get_table_stores()[0]

    compatibility = table_store.metadata["compatibility"]

    assert compatibility["target"] == "apache-iceberg-1.11"
    assert compatibility["rest_catalog"] == {
        "type": "rest",
        "pyiceberg_ref_strategy": "uri-path",
    }
    assert compatibility["checks"] == [
        "rest-catalog-type",
        "pyiceberg-ref-in-uri",
        "warehouse-configured",
        "s3-path-style-access",
    ]
