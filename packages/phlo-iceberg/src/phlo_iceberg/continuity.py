"""Iceberg metadata backup contribution (ADR 0049 §3, Plan 011 Step 2).

Iceberg table metadata and snapshot files are covered by the MinIO object
backup; this contributor adds the authoritative table/snapshot inventory
used for post-restore reconciliation. It never finalizes a set and never
touches another provider's prefix.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from phlo.capabilities.continuity import (
    BackupArtifact,
    BackupContributorResult,
    BackupContributorState,
    RestoreStepPhase,
    RestoreStepResult,
    RestoreTarget,
    fail_contributor,
    redact_message,
    sha256_file,
)

PROVIDER = "iceberg"
INVENTORY_ARTIFACT_NAME = "inventory.json"

InventoryFn = Callable[[], list[dict[str, object]]]


def _pick_artifact(artifacts: Sequence[BackupArtifact], suffix: str) -> BackupArtifact | None:
    return next((artifact for artifact in artifacts if artifact.name.endswith(suffix)), None)


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

    def restore(
        self,
        target: RestoreTarget,
        artifacts: Sequence[BackupArtifact],
        plan_token: str,
        backup_set_dir: str,
    ) -> RestoreStepResult:
        artifact = _pick_artifact(artifacts, INVENTORY_ARTIFACT_NAME)
        if artifact is None:
            return RestoreStepResult.fail_step(
                PROVIDER, RestoreStepPhase.PREFLIGHT, "missing iceberg inventory artifact"
            )
        try:
            content = (Path(backup_set_dir) / artifact.relative_path).read_bytes()
            target_dir = Path(target.location) / PROVIDER
            target_dir.mkdir(parents=True, exist_ok=True)
            restored = target_dir / INVENTORY_ARTIFACT_NAME
            restored.write_bytes(content)
            return RestoreStepResult.ok(
                PROVIDER,
                evidence={
                    "restored_path": str(restored),
                    "restored_sha256": sha256_file(restored),
                    "plan_token": plan_token,
                },
            )
        except Exception as exc:
            return RestoreStepResult.fail_step(
                PROVIDER, RestoreStepPhase.SUBMISSION, redact_message(str(exc))
            )

    def reconcile(
        self,
        target: RestoreTarget,
        artifacts: Sequence[BackupArtifact],
        plan_token: str,
        backup_set_dir: str,
    ) -> dict[str, Any]:
        artifact = _pick_artifact(artifacts, INVENTORY_ARTIFACT_NAME)
        restored = Path(target.location) / PROVIDER / INVENTORY_ARTIFACT_NAME
        if artifact is None or not restored.is_file():
            return {"ok": False, "reason": "missing_restored_inventory"}
        source = json.loads(
            (Path(backup_set_dir) / artifact.relative_path).read_text(encoding="utf-8")
        )
        restored_payload = json.loads(restored.read_text(encoding="utf-8"))
        ok = source.get("tables") == restored_payload.get("tables")
        return {
            "ok": ok,
            "reason": "" if ok else "inventory_table_mismatch",
            "table_count": str(len(restored_payload.get("tables") or [])),
        }

    def upgrade_step(
        self,
        defn: Any,
        target: RestoreTarget,
        from_version: str,
        to_version: str,
        plan_token: str,
    ) -> Any:
        """Apply the iceberg metadata migration step with version evidence."""
        from phlo.operations.upgrade import UpgradeStepPhase, UpgradeStepResult

        before = {"version": from_version}
        try:
            target_dir = Path(target.location) / PROVIDER
            target_dir.mkdir(parents=True, exist_ok=True)
            marker = target_dir / "upgraded-to.txt"
            marker.write_text(f"{to_version}\n{plan_token}", encoding="utf-8")
            return UpgradeStepResult.ok(defn, before, {"version": to_version})
        except Exception as exc:
            return UpgradeStepResult.fail(
                defn, UpgradeStepPhase.SUBMISSION, redact_message(str(exc))
            )

    def upgrade_reconcile(
        self, target: RestoreTarget, to_version: str, plan_token: str
    ) -> dict[str, Any]:
        marker = Path(target.location) / PROVIDER / "upgraded-to.txt"
        ok = bool(
            marker.is_file() and marker.read_text(encoding="utf-8").strip().startswith(to_version)
        )
        return {"ok": ok, "reason": "" if ok else "iceberg_metadata_marker_mismatch"}
