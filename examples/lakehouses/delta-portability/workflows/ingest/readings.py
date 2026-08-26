"""DLT ingestion of hourly compressed NDJSON telemetry deliveries into Delta.

Each operating hour delivers one gzip-compressed NDJSON file. Gateway
retransmissions duplicate ``message_id`` values inside the raw append stream;
late stragglers carry an ``ingested_from_hour`` later than their true
``event_hour``. The normalize stage owns deduplication and late detection.

Delta routing is pinned per asset with ``capabilities={"table_store": "delta"}``
so the portability comparison against the Iceberg sibling is a one-value flip.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import dlt
import pandas as pd
import phlo
from phlo.contracts import SLA, Consumer

from workflows.quality.operational import (
    assert_duplicate_ratio_within_threshold,
    assert_event_date_matches_hour,
    assert_sequence_monotonic,
)
from workflows.schemas.telemetry import TelemetryReadingSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TELEMETRY_DIR = PROJECT_ROOT / "generated-data" / "telemetry"
DELTA_ROUTING = {"table_store": "delta"}


def hour_files(telemetry_dir: Path = TELEMETRY_DIR) -> list[Path]:
    """Return every hourly delivery file ordered by hour label."""
    return sorted(telemetry_dir.glob("hour=*/readings.ndjson.gz"))


def read_hour_file(path: Path) -> pd.DataFrame:
    """Decompress one delivery file into a frame."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return pd.DataFrame(rows)


def read_readings(telemetry_dir: Path = TELEMETRY_DIR, partition_date: str = "") -> pd.DataFrame:
    """Read delivery files, optionally restricted to one calendar day.

    ``partition_date`` is a ``YYYY-MM-DD`` label; an empty string reads the
    whole directory (used by diagnostics and backfills).
    """
    frames: list[pd.DataFrame] = []
    for path in hour_files(telemetry_dir):
        hour_label = path.parent.name.removeprefix("hour=")
        if partition_date and not hour_label.startswith(partition_date):
            continue
        frame = read_hour_file(path)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        raise FileNotFoundError(
            f"No telemetry deliveries found for partition '{partition_date or '*'}' "
            f"under {telemetry_dir}"
        )
    return pd.concat(frames, ignore_index=True)


@phlo.ingest.dlt(
    table_name="telemetry_readings",
    unique_key="message_id",
    validation_schema=TelemetryReadingSchema,
    group="ingest",
    capabilities=DELTA_ROUTING,
    quality_checks=[
        assert_sequence_monotonic,
        assert_duplicate_ratio_within_threshold,
        assert_event_date_matches_hour,
    ],
    freshness_hours=(2, 4),
    merge_strategy="append",
    partition_spec=[("event_date", "identity")],
    strict_validation=True,
    max_runtime_seconds=900,
    max_retries=5,
    retry_delay_seconds=30,
    add_metadata_columns=True,
    owner="fleet-operations",
    consumers=[
        Consumer(name="reliability", usage="device health monitoring"),
        Consumer(name="facilities", usage="site coverage reports"),
    ],
    sla=SLA(freshness_hours=4, quality_threshold=0.995, notify=["fleet-operations"]),
)
def telemetry_readings(partition_date: str) -> object:
    """Append one day of hourly deliveries; retransmissions stay in raw."""
    return dlt.resource(
        read_readings(partition_date=partition_date).to_dict("records"),
        name="telemetry_readings",
    )
