"""Operational checks for the telemetry pipeline.

Every validator runs on plain DataFrames so pytest exercises it against
generated fixtures and operators can run it as a diagnostic against live
tables. Physical bounds are enforced separately by the Pandera contract on
the ingestion assets.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pandas as pd

MAX_DUPLICATE_RATIO = 0.02
MAX_FILES_PER_HOUR = 24


def assert_sequence_monotonic(readings: pd.DataFrame) -> None:
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
        raise ValueError(f"Sequence numbers regress within device hour batch: {regressions[:3]}")


def assert_duplicate_ratio_within_threshold(
    readings: pd.DataFrame, max_ratio: float = MAX_DUPLICATE_RATIO
) -> None:
    """Retransmitted message ids must stay a small fraction of each batch."""
    total = len(readings)
    if total == 0:
        raise ValueError("Empty batch cannot be screened for duplicates")
    counts = Counter(readings.message_id)
    duplicates = sum(count - 1 for count in counts.values() if count > 1)
    ratio = duplicates / total
    if ratio > max_ratio:
        offenders = [message_id for message_id, count in counts.most_common() if count > 1][:5]
        raise ValueError(
            f"Duplicate ratio {ratio:.3f} exceeds threshold {max_ratio}: offenders {offenders}"
        )


def assert_registered_devices_only(readings: pd.DataFrame, devices: pd.DataFrame) -> None:
    """Every reading must reference a registered device."""
    unknown = set(readings.device_id).difference(set(devices.device_id))
    if unknown:
        raise ValueError(f"Telemetry references unregistered devices: {sorted(unknown)}")


def assert_file_count_within_threshold(
    telemetry_dir: Path, max_files_per_hour: int = MAX_FILES_PER_HOUR
) -> None:
    """Hourly partitions must stay under the file-count maintenance threshold."""
    pressure = []
    for hour_dir in sorted(telemetry_dir.glob("hour=*")):
        file_count = sum(1 for _ in hour_dir.glob("*.ndjson.gz"))
        if file_count > max_files_per_hour:
            pressure.append((hour_dir.name, file_count))
    if pressure:
        raise ValueError(
            f"File-count pressure exceeds {max_files_per_hour} files per hour: "
            f"{pressure}. Compact or consolidate the partition before ingesting."
        )
