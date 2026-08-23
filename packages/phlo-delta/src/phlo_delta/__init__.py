"""Phlo Delta Lake package for Delta Lake table format support.

Provides Delta Lake table storage for Phlo: table management, schema
conversion, schema migration, and resource provider integration. ``__all__``
lists the public API exports.

Example:
    from phlo_delta import DeltaResource, get_settings

    settings = get_settings()
    resource = DeltaResource()
    table = resource.get_table("raw.events")
"""

from phlo_delta.helpers import (
    identity_partition,
    load_table_schema,
    maintenance_recommendations,
    recommend_table_maintenance,
    table_exists,
)
from phlo_delta.plugin import DeltaResourceProvider
from phlo_delta.resource import DeltaResource
from phlo_delta.schema_conversion import SchemaConversionError, pandera_to_delta
from phlo_delta.schema_migrator import DeltaSchemaMigrator
from phlo_delta.settings import DeltaSettings, get_settings
from phlo_delta.tables import (
    append_to_table,
    delete_rows_from_table,
    ensure_table,
    expire_snapshots,
    get_table_stats,
    list_table_versions,
    merge_to_table,
    overwrite_table,
    remove_orphan_files,
    rollback_table_to_version,
)

__all__ = [
    "DeltaResource",
    "DeltaResourceProvider",
    "DeltaSchemaMigrator",
    "DeltaSettings",
    "SchemaConversionError",
    "append_to_table",
    "delete_rows_from_table",
    "ensure_table",
    "expire_snapshots",
    "get_settings",
    "get_table_stats",
    "identity_partition",
    "list_table_versions",
    "load_table_schema",
    "maintenance_recommendations",
    "merge_to_table",
    "overwrite_table",
    "pandera_to_delta",
    "recommend_table_maintenance",
    "remove_orphan_files",
    "rollback_table_to_version",
    "table_exists",
]
