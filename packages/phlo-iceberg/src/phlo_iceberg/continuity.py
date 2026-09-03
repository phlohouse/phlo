"""Iceberg metadata backup contribution (ADR 0049 §3, Plan 011 Step 2).

Iceberg table metadata and snapshot files are covered by the MinIO object
backup; this contributor adds the authoritative table/snapshot inventory
used for post-restore reconciliation. It never finalizes a set and never
touches another provider's prefix.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from phlo.capabilities.continuity import (
    BackupArtifact,
    BackupContributorResult,
    BackupContributorState,
    fail_contributor,
    redact_message,
    sha256_file,
)

PROVIDER = "iceberg"
INVENTORY_ARTIFACT_NAME = "inventory.json"

InventoryFn = Callable[[], list[dict[str, object]]]


def _default_inventory() -> list[dict[str, object]]:
    """Scan the Iceberg catalog for tables and their snapshot state."""
    from phlo_iceberg.catalog import list_tables
    from phlo_iceberg.tables import get_table_stats

    inventory: list[dict[str, object]] = []
    for table_name in list_tables():
        stats = get_table_stats(table_name)
        inventory.append(
            {
                "table_name": table_name,
                "snapshot_id": stats.get("snapshot_id"),
                "records": stats.get("total_records"),
                "size_bytes": stats.get("total_size_bytes"),
            }
        )
    inventory.sort(key=lambda item: str(item["table_name"]))
    return inventory


class IcebergBackupContributor:
    """Provider-owned contributor producing the table/snapshot inventory."""

    def __init__(self, inventory_fn: InventoryFn | None = None) -> None:
        self._inventory_fn = inventory_fn

    def contribute(self, destination: Path, operation_id: str) -> BackupContributorResult:
        """Capture the inventory beneath ``destination`` (iceberg prefix)."""
        destination = Path(destination)
        try:
            inventory_fn = self._inventory_fn or _default_inventory
            inventory = inventory_fn()
            payload = {
                "schema_version": "1",
                "operation_id": operation_id,
                "tables": inventory,
            }
            destination.mkdir(parents=True, exist_ok=True)
            artifact_path = destination / INVENTORY_ARTIFACT_NAME
            artifact_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
            )
        except Exception as exc:
            return fail_contributor(PROVIDER, redact_message(str(exc)), operation_id)
        artifact = BackupArtifact(
            provider=PROVIDER,
            name=INVENTORY_ARTIFACT_NAME,
            relative_path=f"{PROVIDER}/{INVENTORY_ARTIFACT_NAME}",
            size_bytes=artifact_path.stat().st_size,
            sha256=sha256_file(artifact_path),
            metadata={"operation_id": operation_id, "table_count": str(len(inventory))},
        )
        return BackupContributorResult(
            provider=PROVIDER,
            state=BackupContributorState.SUCCEEDED,
            artifacts=(artifact,),
            operation_id=operation_id,
        )
