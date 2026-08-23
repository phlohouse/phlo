"""Backfill planning helpers.

Builds daily partition BackfillPlan objects over partition_range(); plans are
frozen value objects and dry_run_backfill() only summarizes — nothing here
executes a backfill.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from phlo.helpers.partitions import partition_range


@dataclass(frozen=True, slots=True)
class BackfillPlan:
    """A lightweight partition backfill plan."""

    asset_key: str
    partitions: list[str]
    parameters: dict[str, str] = field(default_factory=dict)

    @property
    def partition_count(self) -> int:
        """Return number of planned partitions."""
        return len(self.partitions)


def build_backfill_plan(
    asset_key: str,
    *,
    start: str,
    end: str,
    parameters: dict[str, str] | None = None,
) -> BackfillPlan:
    """Build a daily partition backfill plan."""
    return BackfillPlan(
        asset_key=asset_key,
        partitions=partition_range(start, end),
        parameters=parameters or {},
    )


def dry_run_backfill(plan: BackfillPlan) -> dict[str, object]:
    """Return a serializable dry-run summary."""
    return {
        "asset_key": plan.asset_key,
        "partition_count": plan.partition_count,
        "first_partition": plan.partitions[0] if plan.partitions else None,
        "last_partition": plan.partitions[-1] if plan.partitions else None,
        "partitions": plan.partitions,
    }


def rerun_failed_partitions(run_history: list[dict[str, object]]) -> list[str]:
    """Extract failed partition keys from simple run-history dictionaries."""
    return sorted(
        {
            str(run.get("partition_key"))
            for run in run_history
            if run.get("status") == "failed" and run.get("partition_key")
        }
    )
