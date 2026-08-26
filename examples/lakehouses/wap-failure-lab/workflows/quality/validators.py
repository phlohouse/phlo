"""Domain quality checks for the WAP failure lab.

Every validator is a plain DataFrame callable following the
``quality_checks`` protocol: return ``None`` when the batch passes and a
violation string when it fails. Under ``strict_validation=True`` each check
becomes a blocking asset check that gates WAP promotion; under
``strict_validation=False`` the same violation is logged while the run still
succeeds. Staleness is measured against the batch's own partition label, so
checks stay deterministic without wall-clock access.
"""

from __future__ import annotations

import pandas as pd

STALENESS_MAX_DAYS = 7


def assert_batch_ids_unique(batches: pd.DataFrame) -> str | None:
    """Every batch id may appear at most once inside one delivery."""
    duplicated = batches.batch_id[batches.batch_id.duplicated()]
    if not duplicated.empty:
        offenders = sorted(duplicated.unique().tolist())
        return f"batch_id repeated within delivery: {offenders}"
    return None


def assert_recordings_near_partition(
    batches: pd.DataFrame, max_lag_days: int = STALENESS_MAX_DAYS
) -> str | None:
    """Recorded timestamps must sit within the partition window.

    ``batch_date`` labels the delivery partition; a reading stamped more than
    ``max_lag_days`` before its partition (or in the future) is stale backfill.
    """
    recorded = pd.to_datetime(batches.recorded_at, utc=True).dt.floor("D")
    partition = pd.to_datetime(batches.batch_date, utc=True).dt.floor("D")
    lag_days = (partition - recorded).dt.days
    stale = batches[~lag_days.between(0, max_lag_days)]
    if not stale.empty:
        details = [
            f"{row.batch_id}@{row.recorded_at} vs partition {row.batch_date}"
            for row in stale.itertuples()
        ]
        return (
            f"recordings outside the {max_lag_days}-day partition window "
            f"(stale or future): {details[:5]}"
        )
    return None
