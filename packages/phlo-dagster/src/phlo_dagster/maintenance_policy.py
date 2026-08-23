"""Policy models and evaluation for automated Iceberg table maintenance.

This module defines the data models and evaluation logic for policy-driven
Iceberg table maintenance. Policies specify thresholds that trigger maintenance
operations like snapshot expiration and file optimization.

Policy Types:
    - ExpireSnapshotsPolicy: Thresholds for snapshot cleanup
    - OptimizePolicy: Thresholds for file compaction
    - NamespacePolicy: Complete policy scoped to a catalog namespace

Thresholds:
    - Snapshot expiration: snapshot_count_gt, older_than_days, retain_last
    - File optimization: avg_file_size_mb_lt

Evaluation Logic:
    Table statistics are compared against policy thresholds to determine
    required maintenance actions (TableAction). Multiple actions can be
    triggered for a single table.

Configuration:
    Policies are loaded from YAML files with structure::

        policies:
          - namespace: raw
            ref: main
            expire:
              snapshot_count_gt: 20
              older_than_days: 7
              retain_last: 5
            optimize:
              avg_file_size_mb_lt: 64.0

Example:
    Loading and evaluating policies::

        from phlo_dagster.maintenance_policy import load_policies, evaluate_table

        policies = load_policies("maintenance_policy.yaml")
        for policy in policies:
            for table in list_tables(policy.namespace, policy.ref):
                stats = get_table_stats(table, policy.ref)
                action = evaluate_table(table, stats, policy)
                if action.expire_snapshots:
                    # Trigger snapshot expiration
                    pass

"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from phlo.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ExpireSnapshotsPolicy:
    """Thresholds for triggering snapshot expiration."""

    snapshot_count_gt: int = 20
    older_than_days: int = 7
    retain_last: int = 5


@dataclass(frozen=True, slots=True)
class OptimizePolicy:
    """Thresholds for triggering file compaction."""

    avg_file_size_mb_lt: float = 64.0


@dataclass(frozen=True, slots=True)
class NamespacePolicy:
    """Maintenance policy scoped to a catalog namespace."""

    namespace: str
    expire: ExpireSnapshotsPolicy | None = None
    optimize: OptimizePolicy | None = None
    ref: str = "main"


@dataclass(frozen=True, slots=True)
class TableAction:
    """Maintenance actions determined for a single table."""

    table_name: str
    expire_snapshots: bool = False
    optimize: bool = False


def evaluate_table(
    table_name: str,
    stats: dict[str, Any],
    policy: NamespacePolicy,
) -> TableAction:
    """Evaluate table stats against policy thresholds.

    ``stats`` carries snapshot_count, total_size_mb, and file_count keys;
    ``policy`` holds optional expire/optimize thresholds per namespace. Returns
    a TableAction describing which maintenance operations to run.
    """
    expire = False
    optimize = False

    if policy.expire is not None:
        snapshot_count = stats.get("snapshot_count", 0)
        if snapshot_count > policy.expire.snapshot_count_gt:
            expire = True

    if policy.optimize is not None:
        file_count = stats.get("file_count", 0)
        total_size_mb = stats.get("total_size_mb", 0.0)
        if file_count > 0:
            avg_file_size_mb = total_size_mb / file_count
            if avg_file_size_mb < policy.optimize.avg_file_size_mb_lt:
                optimize = True

    return TableAction(
        table_name=table_name,
        expire_snapshots=expire,
        optimize=optimize,
    )


def load_policies(path: str | Path) -> list[NamespacePolicy]:
    """Load maintenance policies from a YAML file.

    Expected format::

        policies:
          - namespace: raw
            expire:
              snapshot_count_gt: 20
              older_than_days: 7
              retain_last: 5
            optimize:
              avg_file_size_mb_lt: 64.0
          - namespace: curated
            expire:
              snapshot_count_gt: 10
    """
    try:
        import yaml
    except Exception as exc:  # noqa: BLE001 - runtime guidance for optional dependency
        raise RuntimeError(
            "Policy loading requires PyYAML. Install phlo-dagster[policies] or pyyaml."
        ) from exc

    policy_path = Path(path)
    data = yaml.safe_load(policy_path.read_text())
    policies: list[NamespacePolicy] = []

    for entry in (data or {}).get("policies", []):
        expire = None
        optimize = None

        if "expire" in entry:
            expire = ExpireSnapshotsPolicy(**entry["expire"])
        if "optimize" in entry:
            optimize = OptimizePolicy(**entry["optimize"])

        policies.append(
            NamespacePolicy(
                namespace=entry["namespace"],
                expire=expire,
                optimize=optimize,
                ref=entry.get("ref", "main"),
            )
        )

    logger.info("maintenance_policies_loaded", policy_count=len(policies), path=str(policy_path))
    return policies
