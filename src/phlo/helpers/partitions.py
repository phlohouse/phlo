"""Partition key and partition-scope helpers.

Partition keys are ISO daily dates (YYYY-MM-DD) with timezone-aware derivation;
PartitionScope expresses an explicit range, a rolling window, or full-table
coverage, and expected_partitions() reconciles a desired range against
existing keys.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class PartitionScope:
    """Reusable partition scope for reads, checks, and writes."""

    partition_key: str | None = None
    partition_column: str = "_phlo_partition_date"
    start: str | None = None
    end: str | None = None
    rolling_window_days: int | None = None
    full_table: bool = False

    def is_scoped(self) -> bool:
        """Return whether this scope restricts rows."""
        return not self.full_table and bool(
            self.partition_key or self.start or self.end or self.rolling_window_days
        )


def partition_key_today(*, timezone: str = "UTC") -> str:
    """Return today's daily partition key in the requested timezone."""
    return datetime.now(ZoneInfo(timezone)).date().isoformat()


def parse_partition_key(partition_key: str) -> date:
    """Parse a daily partition key."""
    return date.fromisoformat(partition_key)


def partition_range(
    start: str | date,
    end: str | date,
    *,
    inclusive: bool = True,
) -> list[str]:
    """Return daily partition keys between start and end."""
    start_date = date.fromisoformat(start) if isinstance(start, str) else start
    end_date = date.fromisoformat(end) if isinstance(end, str) else end
    if end_date < start_date:
        return []
    limit = end_date if inclusive else end_date - timedelta(days=1)
    keys: list[str] = []
    current = start_date
    while current <= limit:
        keys.append(current.isoformat())
        current += timedelta(days=1)
    return keys


def previous_partition(partition_key: str, *, days: int = 1) -> str:
    """Return the previous daily partition key."""
    return (parse_partition_key(partition_key) - timedelta(days=days)).isoformat()


def rolling_partition_range(
    *,
    days: int,
    end: str | date | None = None,
    timezone: str = "UTC",
) -> list[str]:
    """Return a trailing daily partition range ending at end or today."""
    end_date = (
        date.fromisoformat(end)
        if isinstance(end, str)
        else end
        if end is not None
        else datetime.now(ZoneInfo(timezone)).date()
    )
    start_date = end_date - timedelta(days=max(days - 1, 0))
    return partition_range(start_date, end_date)


def partition_scope(
    partition_key: str | None = None,
    *,
    partition_column: str = "_phlo_partition_date",
    start: str | None = None,
    end: str | None = None,
    rolling_window_days: int | None = None,
    full_table: bool = False,
) -> PartitionScope:
    """Build a normalized partition scope."""
    return PartitionScope(
        partition_key=partition_key,
        partition_column=partition_column,
        start=start,
        end=end,
        rolling_window_days=rolling_window_days,
        full_table=full_table,
    )


def expected_partitions(
    *,
    start: str,
    end: str | None = None,
    existing: Iterable[str] | None = None,
) -> dict[str, list[str]]:
    """Compare expected daily partitions with an optional existing set."""
    keys = partition_range(start, end or partition_key_today())
    existing_set = set(existing or [])
    return {
        "expected": keys,
        "existing": sorted(existing_set),
        "missing": [key for key in keys if key not in existing_set],
    }


def timestamp_partition_key(value: datetime | None = None, *, timezone: str = "UTC") -> str:
    """Return a daily partition key for a timestamp."""
    if value is None:
        value = datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo(timezone)).date().isoformat()
