"""Apache Iceberg compatibility checks for Phlo's lakehouse surface.

Validates PyIceberg REST catalog configuration against the pinned Iceberg
target (currently 1.11). Nessie-backed PyIceberg clients encode the ref in
the REST URI path while Trino uses a prefix property, so prefix-style refs
are rejected here. Violations raise ValueError before any client is built.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

APACHE_ICEBERG_COMPATIBILITY_TARGET = "1.11"
COMPATIBILITY_TARGET = f"apache-iceberg-{APACHE_ICEBERG_COMPATIBILITY_TARGET}"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_pyiceberg_rest_catalog_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate Phlo's PyIceberg REST catalog config for Iceberg 1.11 expectations.

    Nessie-backed PyIceberg clients encode the ref in the REST URI path, for example
    ``http://nessie:19120/iceberg/main``. Trino uses ``iceberg.rest-catalog.prefix``
    for refs instead, so this guard rejects prefix-style properties in PyIceberg config.
    """
    uri = str(config.get("uri", ""))
    path_parts = [part for part in urlparse(uri).path.split("/") if part]

    _require(config.get("type") == "rest", "Iceberg 1.11 compatibility requires REST catalog type")
    _require("iceberg" in path_parts, "REST catalog URI must target Nessie's /iceberg surface")
    _require(
        len(path_parts) > path_parts.index("iceberg") + 1,
        "PyIceberg REST catalog refs must be encoded in the URI path",
    )
    _require(
        "prefix" not in config and "iceberg.rest-catalog.prefix" not in config,
        "PyIceberg REST catalog refs must be encoded in the URI path, not prefix properties",
    )
    _require(bool(config.get("warehouse")), "Iceberg REST catalog config must include warehouse")
    _require(
        str(config.get("s3.path-style-access", "")).lower() == "true",
        "S3-compatible Iceberg storage requires path-style access",
    )

    return {
        "compatible": True,
        "target": COMPATIBILITY_TARGET,
        "checks": [
            "rest-catalog-type",
            "nessie-iceberg-rest-uri",
            "pyiceberg-ref-in-uri",
            "warehouse-configured",
            "s3-path-style-access",
        ],
    }
