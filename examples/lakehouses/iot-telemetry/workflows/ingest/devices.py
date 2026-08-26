"""DLT ingestion of the device registry database.

The registry is a small SQLite database managed beside the fixtures. Both
reference tables merge by primary key, so repeated registry refreshes stay
idempotent even though the source itself has no change feed.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import dlt
import phlo
from phlo.contracts import SLA, Consumer

from workflows.schemas.telemetry import DeviceRegistrySchema, SiteDirectorySchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY_DB = PROJECT_ROOT / "generated-data" / "device_registry.sqlite"


def registry_db() -> Path:
    """Return the registry database path (override with IOT_REGISTRY_DB)."""
    return Path(os.environ.get("IOT_REGISTRY_DB", DEFAULT_REGISTRY_DB))


def _registry_rows(table: str, database: Path) -> list[dict[str, object]]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]
    finally:
        connection.close()


@phlo.ingest.dlt(
    table_name="device_registry",
    unique_key="device_id",
    validation_schema=DeviceRegistrySchema,
    group="registry",
    partitioned=False,
    freshness_hours=(168, 192),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=120,
    max_retries=2,
    retry_delay_seconds=60,
    add_metadata_columns=True,
    owner="fleet-management",
    consumers=[
        Consumer(name="reliability", usage="known-device validation"),
        Consumer(name="facilities", usage="site coverage reporting"),
    ],
    sla=SLA(freshness_hours=192, quality_threshold=1.0),
)
def device_registry(partition_date: str) -> object:
    """Merge the current fleet registry snapshot."""
    del partition_date
    return dlt.resource(_registry_rows("devices", registry_db()), name="device_registry")


@phlo.ingest.dlt(
    table_name="site_directory",
    unique_key="site_id",
    validation_schema=SiteDirectorySchema,
    group="registry",
    partitioned=False,
    freshness_hours=(720, 744),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=60,
    max_retries=1,
    retry_delay_seconds=60,
    add_metadata_columns=True,
    owner="facilities",
    consumers=[Consumer(name="facilities", usage="published site reports")],
    sla=SLA(freshness_hours=744, quality_threshold=1.0),
)
def site_directory(partition_date: str) -> object:
    """Merge the site reference directory."""
    del partition_date
    return dlt.resource(_registry_rows("sites", registry_db()), name="site_directory")
