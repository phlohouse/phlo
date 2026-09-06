"""Nessie-to-Polaris migration (dry-run by default).

Inventories tables on the Nessie REST catalog and, only after explicit
confirmation, registers them into the Polaris REST catalog. The migration is
metadata-only: Nessie catalog metadata and Iceberg object data are never
modified or deleted, so the source project remains fully intact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from phlo.logging import get_logger
from phlo_polaris.catalog_backend import current_snapshot_id

logger = get_logger(__name__)


@dataclass(frozen=True)
class MigrationPlanEntry:
    """One table inventoried for migration."""

    namespace: str
    table_name: str
    location: str | None = None
    current_snapshot_id: int | None = None


@dataclass(frozen=True)
class MigrationPlan:
    """Inventory of tables a confirmed migration would register."""

    entries: list[MigrationPlanEntry] = field(default_factory=list)
    dry_run: bool = True

    @property
    def table_count(self) -> int:
        """Return the number of inventoried tables."""
        return len(self.entries)


def _load_source_catalog() -> Any:
    """Load the Nessie REST catalog as the migration source."""
    import os

    from pyiceberg.catalog import load_catalog

    host = os.environ.get("NESSIE_HOST", "nessie")
    port = os.environ.get("NESSIE_PORT", "19120")
    ref = os.environ.get("NESSIE_DEFAULT_REF", "main")
    return load_catalog(
        name="polaris_migration_source_nessie",
        **{
            "type": "rest",
            "uri": f"http://{host}:{port}/iceberg/{ref}",
            "warehouse": "warehouse",
            "s3.endpoint": os.environ.get("ICEBERG_S3_ENDPOINT", "http://minio:9000/"),
            "s3.access-key-id": os.environ.get("ICEBERG_S3_ACCESS_KEY", "minio"),
            "s3.secret-access-key": os.environ.get("ICEBERG_S3_SECRET_KEY", "minio123"),
            "s3.path-style-access": "true",
            "s3.region": os.environ.get("ICEBERG_S3_REGION", "us-east-1"),
        },
    )


def _load_target_catalog() -> Any:
    """Load the Polaris REST catalog as the migration target."""
    from phlo_polaris.catalog_backend import load_pyiceberg_catalog

    return load_pyiceberg_catalog()


def plan_migration(source_catalog: Any | None = None) -> list[MigrationPlanEntry]:
    """Inventory every table the migration would register into Polaris."""
    catalog = source_catalog if source_catalog is not None else _load_source_catalog()
    entries: list[MigrationPlanEntry] = []
    for namespace_tuple in catalog.list_namespaces():
        if not namespace_tuple:
            continue
        namespace = ".".join(namespace_tuple)
        for identifier in catalog.list_tables(namespace):
            table_name = identifier[-1]
            location = None
            snapshot_id = None
            try:
                table = catalog.load_table(identifier)
                location = table.location()
                snapshot_id = current_snapshot_id(table)
            except Exception:
                logger.warning(
                    "polaris_migration_inventory_load_failed",
                    namespace=namespace,
                    table=table_name,
                    exc_info=True,
                )
            entries.append(
                MigrationPlanEntry(
                    namespace=namespace,
                    table_name=table_name,
                    location=location,
                    current_snapshot_id=snapshot_id,
                )
            )
    entries.sort(key=lambda entry: (entry.namespace, entry.table_name))
    return entries


def import_tables(
    entries: list[MigrationPlanEntry],
    *,
    source_catalog: Any | None = None,
    target_catalog: Any | None = None,
) -> list[dict[str, Any]]:
    """Register inventoried tables into Polaris without touching the source.

    Registration points Polaris at the existing Iceberg metadata files; no
    data is copied and no Nessie metadata is written or removed.
    """
    source = source_catalog if source_catalog is not None else _load_source_catalog()
    target = target_catalog if target_catalog is not None else _load_target_catalog()
    results: list[dict[str, Any]] = []
    for entry in entries:
        identifier = (*entry.namespace.split("."), entry.table_name)
        try:
            source_table = source.load_table(identifier)
            target.create_namespace_if_not_exists(entry.namespace)
            target.register_table(entry.table_name, source_table.metadata_location)
            results.append(
                {
                    "namespace": entry.namespace,
                    "table": entry.table_name,
                    "registered": True,
                }
            )
        except Exception as exc:
            logger.warning(
                "polaris_migration_register_failed",
                namespace=entry.namespace,
                table=entry.table_name,
                exc_info=True,
            )
            results.append(
                {
                    "namespace": entry.namespace,
                    "table": entry.table_name,
                    "registered": False,
                    "error": str(exc),
                }
            )
    return results
