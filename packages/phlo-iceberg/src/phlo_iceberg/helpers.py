"""Lightweight Iceberg helper utilities.

These helpers compose the table APIs in :mod:`phlo_iceberg.tables` into small,
ergonomic operations without opening catalog connections at import time.
"""

from __future__ import annotations

from typing import Any, Literal

PartitionTransform = Literal["identity", "day", "hour", "month", "year"]
PartitionSpecInput = list[tuple[str, PartitionTransform]]


def table_exists(table_name: str, ref: str = "main") -> bool:
    """Return whether an Iceberg table can be loaded from the catalog."""
    from phlo_iceberg.catalog import get_catalog

    try:
        get_catalog(ref=ref).load_table(table_name)
    except Exception:
        return False
    return True


def identity_partition(*columns: str) -> PartitionSpecInput:
    """Build an identity partition spec for one or more columns."""
    return [(column, "identity") for column in columns]


def temporal_partition(column: str, transform: PartitionTransform = "day") -> PartitionSpecInput:
    """Build a one-column temporal partition spec."""
    if transform not in {"day", "hour", "month", "year"}:
        raise ValueError(
            f"Temporal partition transform must be day/hour/month/year, got {transform}"
        )
    return [(column, transform)]


def partition_spec(*entries: tuple[str, PartitionTransform]) -> PartitionSpecInput:
    """Validate and return an Iceberg partition spec tuple list."""
    allowed = {"identity", "day", "hour", "month", "year"}
    spec: PartitionSpecInput = []
    for column, transform in entries:
        if not column:
            raise ValueError("Partition column names must be non-empty")
        if transform not in allowed:
            raise ValueError(f"Unknown Iceberg partition transform: {transform}")
        spec.append((column, transform))
    return spec


def maintenance_recommendations(
    stats: dict[str, Any],
    *,
    max_file_count: int = 1000,
    max_snapshot_count: int = 50,
    min_avg_file_size_mb: float = 32.0,
) -> list[str]:
    """Return conservative maintenance recommendations from table stats."""
    recommendations: list[str] = []
    file_count = int(stats.get("file_count") or 0)
    snapshot_count = int(stats.get("snapshot_count") or 0)
    total_size_mb = float(stats.get("total_size_mb") or 0.0)
    avg_file_size_mb = total_size_mb / file_count if file_count else 0.0

    if snapshot_count > max_snapshot_count:
        recommendations.append("expire_snapshots")
    if file_count > max_file_count:
        recommendations.append("remove_orphan_files")
    if file_count > 1 and avg_file_size_mb < min_avg_file_size_mb:
        recommendations.append("consider_compaction")

    return recommendations


def recommend_table_maintenance(table_name: str, ref: str = "main", **kwargs: Any) -> list[str]:
    """Load Iceberg table stats and return maintenance recommendations."""
    from phlo_iceberg.tables import get_table_stats

    return maintenance_recommendations(get_table_stats(table_name, ref=ref), **kwargs)


__all__ = [
    "PartitionSpecInput",
    "PartitionTransform",
    "identity_partition",
    "maintenance_recommendations",
    "partition_spec",
    "recommend_table_maintenance",
    "table_exists",
    "temporal_partition",
]
