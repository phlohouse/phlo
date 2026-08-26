"""DLT ingestion of quarter-hour platform-event micro-batches.

The delivery layer re-sends one earlier event verbatim inside each later
batch, so the raw append stream accumulates duplicate ``event_id`` rows.
Replays stay in raw by design; read-time deduplication in the operational
marts keeps aggregates stable across replays.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import dlt
import pandas as pd
import phlo
from phlo.contracts import SLA, Consumer

from workflows.quality.validators import check_event_types_known
from workflows.schemas.contracts import PlatformEventSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVENTS_DIR = PROJECT_ROOT / "generated-data" / "platform_events"


def batch_files(events_dir: Path = EVENTS_DIR) -> list[Path]:
    """Return every micro-batch file ordered by hour label then batch index."""
    return sorted(events_dir.glob("hour=*/batch-*.ndjson.gz"))


def read_batch_file(path: Path) -> pd.DataFrame:
    """Decompress one micro-batch into a frame."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return pd.DataFrame(rows)


def read_platform_events(events_dir: Path = EVENTS_DIR, partition_date: str = "") -> pd.DataFrame:
    """Read micro-batches, optionally restricted to one calendar day.

    ``partition_date`` is a ``YYYY-MM-DD`` label; an empty string reads the
    whole directory (used by diagnostics and backfills).
    """
    frames: list[pd.DataFrame] = []
    for path in batch_files(events_dir):
        hour_label = path.parent.name.removeprefix("hour=")
        if partition_date and not hour_label.startswith(partition_date):
            continue
        frame = read_batch_file(path)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        raise FileNotFoundError(
            f"No platform-event batches found for partition '{partition_date or '*'}' "
            f"under {events_dir}"
        )
    return pd.concat(frames, ignore_index=True)


def with_occurred_hour(frame: pd.DataFrame) -> pd.DataFrame:
    """Add the hourly truncation column used as the identity partition."""
    frame = frame.copy()
    frame["occurred_hour"] = pd.to_datetime(frame["occurred_at"]).dt.floor("h")
    return frame


@phlo.ingest.dlt(
    table_name="platform_events",
    unique_key="event_id",
    validation_schema=PlatformEventSchema,
    group="platform_events",
    freshness_hours=(1, 2),
    merge_strategy="append",
    partition_spec=[("occurred_hour", "identity")],
    strict_validation=True,
    max_runtime_seconds=300,
    max_retries=3,
    retry_delay_seconds=30,
    add_metadata_columns=True,
    owner="platform-observability",
    consumers=[
        Consumer(name="sre", usage="error-rate and latency dashboards"),
        Consumer(name="billing", usage="tenant usage rollups"),
    ],
    sla=SLA(freshness_hours=2, quality_threshold=0.995, notify=["platform-observability"]),
    quality_checks=[check_event_types_known],
)
def platform_events(partition_date: str) -> object:
    """Append one day of quarter-hour micro-batches; replays stay in raw."""
    return dlt.resource(
        with_occurred_hour(read_platform_events(partition_date=partition_date)).to_dict("records"),
        name="platform_events",
    )
