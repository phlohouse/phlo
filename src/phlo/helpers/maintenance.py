"""Maintenance policy helpers for lakehouse tables.

Policy defaults drive recommendations computed from table stats (compaction,
snapshot expiry, orphan cleanup); optimize_table runs only the operations the
supplied table_store actually exposes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class MaintenancePolicy:
    """Table maintenance policy descriptor."""

    snapshot_retention_days: int = 7
    snapshot_retain_last: int = 5
    orphan_retention_days: int = 3
    compact_small_files: bool = True
    target_file_size_mb: int = 512
    metadata: dict[str, Any] = field(default_factory=dict)


def maintenance_policy(**overrides: Any) -> MaintenancePolicy:
    """Build a maintenance policy with explicit overrides."""
    return MaintenancePolicy(**overrides)


def maintenance_recommendations(
    stats: dict[str, Any],
    *,
    policy: MaintenancePolicy | None = None,
) -> list[str]:
    """Recommend maintenance actions from table stats."""
    policy = policy or MaintenancePolicy()
    recommendations: list[str] = []
    file_count = int(stats.get("file_count") or stats.get("files") or 0)
    row_count = int(stats.get("row_count") or stats.get("rows") or 0)
    snapshot_count = int(stats.get("snapshot_count") or stats.get("snapshots") or 0)
    orphan_count = int(stats.get("orphan_count") or stats.get("orphan_files") or 0)
    if policy.compact_small_files and file_count > 0 and row_count / max(file_count, 1) < 10_000:
        recommendations.append("compact_small_files")
    if snapshot_count > policy.snapshot_retain_last:
        recommendations.append("expire_old_snapshots")
    if orphan_count > 0:
        recommendations.append("cleanup_orphan_files")
    return recommendations


def optimize_table(
    table_name: str,
    *,
    table_store: Any,
    policy: MaintenancePolicy | None = None,
) -> dict[str, Any]:
    """Run supported maintenance operations for a table."""
    policy = policy or MaintenancePolicy()
    actions: dict[str, Any] = {}
    if policy.compact_small_files and hasattr(table_store, "compact"):
        actions["compact"] = table_store.compact(table_name=table_name)
    if hasattr(table_store, "vacuum"):
        actions["vacuum"] = table_store.vacuum(
            table_name=table_name,
            retain_hours=policy.orphan_retention_days * 24,
        )
    return actions
