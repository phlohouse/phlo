"""Sync Nessie-discovered tables to OpenMetadata.

This module bridges the Nessie catalog backend (`phlo-nessie`) with the OpenMetadata publisher
(`phlo-openmetadata`).

Example:
    >>> from phlo_openmetadata.nessie_sync import sync_nessie_tables_to_openmetadata
    >>> from phlo.capabilities import resolve_capability
    >>> scanner = resolve_capability("catalog_scanner").provider
    >>> stats = sync_nessie_tables_to_openmetadata(scanner, om_client)

"""

from __future__ import annotations

from typing import Any

from phlo.capabilities.interfaces import CatalogScanner
from phlo.logging import get_logger
from phlo_openmetadata.openmetadata import OpenMetadataClient, OpenMetadataColumn, OpenMetadataTable

logger = get_logger(__name__)


def _map_iceberg_to_openmetadata_type(iceberg_type: str) -> str:
    """Map an Iceberg column type string to an OpenMetadata type."""
    type_map = {
        "boolean": "BOOLEAN",
        "int": "INT",
        "long": "BIGINT",
        "float": "FLOAT",
        "double": "DOUBLE",
        "decimal": "DECIMAL",
        "date": "DATE",
        "time": "TIME",
        "timestamp": "TIMESTAMP",
        "timestamptz": "TIMESTAMPZ",
        "string": "STRING",
        "uuid": "STRING",
        "fixed": "BINARY",
        "binary": "BINARY",
        "struct": "STRUCT",
        "list": "ARRAY",
        "map": "MAP",
    }
    base_type = iceberg_type.split("<")[0].lower()
    return type_map.get(base_type, "STRING")


def nessie_table_metadata_to_openmetadata_table(
    table_metadata: dict[str, Any], description: str | None = None
) -> OpenMetadataTable:
    """Convert Nessie table metadata into an OpenMetadata table payload."""
    table_name = table_metadata.get("name", "unknown")
    schema = (
        table_metadata.get("schema", {}) if isinstance(table_metadata.get("schema"), dict) else {}
    )

    columns: list[OpenMetadataColumn] = []
    for ordinal, field in enumerate(
        schema.get("fields", []) if isinstance(schema.get("fields"), list) else []
    ):
        if not isinstance(field, dict):
            continue
        col_type = _map_iceberg_to_openmetadata_type(str(field.get("type", "unknown")))
        columns.append(
            OpenMetadataColumn(
                name=str(field.get("name", f"col_{ordinal}")),
                dataType=col_type,
                description=field.get("doc"),
                ordinalPosition=ordinal,
            )
        )

    location = None
    props = table_metadata.get("properties")
    if isinstance(props, dict):
        location = props.get("location")

    return OpenMetadataTable(
        name=str(table_name),
        description=description or table_metadata.get("doc"),
        columns=columns or None,
        tableType="Regular",
        location=location,
    )


def sync_nessie_tables_to_openmetadata(
    scanner: CatalogScanner,
    om_client: OpenMetadataClient,
    include_namespaces: list[str] | None = None,
    exclude_namespaces: list[str] | None = None,
) -> dict[str, int]:
    """Sync Nessie tables to OpenMetadata and return aggregate sync stats.

    Per-table sync errors are logged and counted under ``failed`` instead of
    raised, so one bad table does not abort the run.
    """
    stats = {"created": 0, "failed": 0}
    include = set(include_namespaces) if include_namespaces else None
    exclude = set(exclude_namespaces or [])

    catalog = scanner.scan_all_tables()
    for namespace, tables in catalog.items():
        if include is not None and namespace not in include:
            continue
        if namespace in exclude:
            continue

        for table_entry in tables:
            if not isinstance(table_entry, dict):
                continue
            name = table_entry.get("name")
            if not isinstance(name, str):
                continue
            try:
                full_metadata = scanner.get_table_metadata(namespace, name) or table_entry
                om_table = nessie_table_metadata_to_openmetadata_table(full_metadata)
                om_client.create_or_update_table(namespace, om_table)
                stats["created"] += 1
            except Exception as e:
                logger.error("Failed to sync %s.%s: %s", namespace, name, e)
                stats["failed"] += 1

    return stats
