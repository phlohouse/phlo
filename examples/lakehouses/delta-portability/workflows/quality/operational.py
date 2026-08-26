"""Operational checks for the Delta telemetry pipeline.

Validators follow the ``quality_checks`` protocol: return ``None`` when the
batch passes and a violation string when it fails. They run on plain
DataFrames so pytest exercises them against generated fixtures and operators
can run them as diagnostics against live tables. Physical bounds are enforced
separately by the blocking Pandera contracts on the ingestion assets.
"""

from __future__ import annotations

from collections import Counter

import pandas as pd

MAX_DUPLICATE_RATIO = 0.02


def assert_sequence_monotonic(readings: pd.DataFrame) -> str | None:
    """Device sequence numbers must strictly increase as event time advances.

    Retransmissions repeat an identical message verbatim, so the screen runs
    on one version per ``message_id``; only distinct messages can regress.
    """
    distinct = readings.drop_duplicates(subset="message_id", keep="last")
    ordered = distinct.sort_values(["device_id", "event_time"], kind="stable")
    regressions = []
    for device_id, group in ordered.groupby("device_id"):
        sequences = group.sequence_number.tolist()
        for earlier, later in zip(sequences, sequences[1:], strict=False):
            if later <= earlier:
                regressions.append((device_id, earlier, later))
                break
    if regressions:
        return f"sequence numbers regress within device hour batch: {regressions[:3]}"
    return None


def assert_duplicate_ratio_within_threshold(
    readings: pd.DataFrame, max_ratio: float = MAX_DUPLICATE_RATIO
) -> str | None:
    """Retransmitted message ids must stay a small fraction of each batch."""
    total = len(readings)
    if total == 0:
        return "empty batch cannot be screened for duplicates"
    counts = Counter(readings.message_id)
    duplicates = sum(count - 1 for count in counts.values() if count > 1)
    ratio = duplicates / total
    if ratio > max_ratio:
        offenders = [message_id for message_id, count in counts.most_common() if count > 1][:5]
        return f"duplicate ratio {ratio:.3f} exceeds threshold {max_ratio}: offenders {offenders}"
    return None


def assert_event_date_matches_hour(readings: pd.DataFrame) -> str | None:
    """The identity partition column must equal the day truncation of event_hour.

    Delta partitions by raw column values only (identity transforms), so a
    mismatched ``event_date`` would silently file readings under the wrong
    partition instead of failing at planning time like an Iceberg transform.
    """
    event_hour = pd.to_datetime(readings.event_hour, utc=True)
    event_date = pd.to_datetime(readings.event_date, utc=True).dt.normalize()
    mismatched = readings[event_hour.dt.normalize() != event_date]
    if mismatched.empty:
        return None
    offenders = [f"{row.message_id}: {row.event_date}" for row in mismatched.itertuples()][:5]
    return f"event_date disagrees with event_hour day: {offenders}"


def assert_registered_devices_only(readings: pd.DataFrame, devices: pd.DataFrame) -> str | None:
    """Every reading must reference a registered device."""
    unknown = set(readings.device_id).difference(set(devices.device_id))
    if unknown:
        return f"telemetry references unregistered devices: {sorted(unknown)}"
    return None
