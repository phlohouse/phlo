"""Phlo Iceberg package for Apache Iceberg table format support.

This package provides integration with Apache Iceberg for data lake table
management, including catalog operations, table CRUD, schema conversion,
and migration capabilities.

Modules:
    catalog: Iceberg REST catalog management using Nessie.
    tables: Table operations (append, merge, overwrite, snapshots, cleanup).
    resource: IcebergResource dataclass for asset/resource access.
    schema_conversion: Pandera-to-Iceberg schema conversion utilities.
    schema_migrator: Iceberg-backed SchemaMigrator implementation.
    settings: Configuration and settings management.
    plugin: Phlo plugin registration for resource providers.

Example:
    Basic usage of the iceberg package::

        from phlo_iceberg import IcebergResource, get_catalog

        # Get catalog connection
        catalog = get_catalog(ref="main")

        # Use resource for table operations
        iceberg = IcebergResource(ref="main")
        result = iceberg.append_parquet(
            table_name="raw.events",
            data_path="/path/to/data.parquet"
        )

See Also:
    Apache Iceberg: https://iceberg.apache.org/
    PyIceberg: https://py.iceberg.apache.org/

"""

from phlo_iceberg.catalog import get_catalog
from phlo_iceberg.helpers import (
    identity_partition,
    maintenance_recommendations,
    partition_spec,
    recommend_table_maintenance,
    table_exists,
    temporal_partition,
)
from phlo_iceberg.plugin import IcebergResourceProvider
from phlo_iceberg.resource import IcebergResource
from phlo_iceberg.schema_conversion import SchemaConversionError, pandera_to_iceberg
from phlo_iceberg.schema_migrator import IcebergSchemaMigrator
from phlo_iceberg.settings import IcebergSettings, get_settings
from phlo_iceberg.tables import (
    append_to_table,
    ensure_table,
    expire_snapshots,
    get_table_schema,
    get_table_stats,
    merge_to_table,
    remove_orphan_files,
)

__all__ = [
    "append_to_table",
    "ensure_table",
    "expire_snapshots",
    "get_catalog",
    "get_table_schema",
    "get_table_stats",
    "identity_partition",
    "IcebergResource",
    "IcebergResourceProvider",
    "IcebergSchemaMigrator",
    "IcebergSettings",
    "maintenance_recommendations",
    "partition_spec",
    "recommend_table_maintenance",
    "SchemaConversionError",
    "get_settings",
    "merge_to_table",
    "pandera_to_iceberg",
    "remove_orphan_files",
    "table_exists",
    "temporal_partition",
]
