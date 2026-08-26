"""DLT ingestion of hourly access-log deliveries.

Request logs arrive as one file per operating hour. Status codes are held to
a fixed catalog and durations are bounded; the serving marts own read-time
deduplication so replayed hours cannot double-count requests.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import dlt
import pandas as pd
import phlo
from phlo.contracts import SLA, Consumer

from workflows.quality.validators import check_paths_under_api
from workflows.schemas.contracts import AccessLogSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOGS_DIR = PROJECT_ROOT / "generated-data" / "access_logs"


def hour_files(logs_dir: Path = LOGS_DIR) -> list[Path]:
    """Return every hourly request-log file ordered by hour label."""
    return sorted(logs_dir.glob("hour=*/requests.ndjson.gz"))


def read_hour_file(path: Path) -> pd.DataFrame:
    """Decompress one request-log file into a frame."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return pd.DataFrame(rows)


def read_access_logs(logs_dir: Path = LOGS_DIR, partition_date: str = "") -> pd.DataFrame:
    """Read request logs, optionally restricted to one calendar day."""
    frames: list[pd.DataFrame] = []
    for path in hour_files(logs_dir):
        hour_label = path.parent.name.removeprefix("hour=")
        if partition_date and not hour_label.startswith(partition_date):
            continue
        frame = read_hour_file(path)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        raise FileNotFoundError(
            f"No access-log files found for partition '{partition_date or '*'}' under {logs_dir}"
        )
    return pd.concat(frames, ignore_index=True)


@phlo.ingest.dlt(
    table_name="access_logs",
    unique_key="request_id",
    validation_schema=AccessLogSchema,
    group="access_logs",
    freshness_hours=(2, 3),
    merge_strategy="append",
    partition_spec=[("occurred_at", "hour")],
    strict_validation=True,
    max_runtime_seconds=300,
    max_retries=3,
    retry_delay_seconds=30,
    add_metadata_columns=True,
    owner="platform-observability",
    consumers=[
        Consumer(name="sre", usage="throughput and p95 latency panels"),
        Consumer(name="support", usage="per-tenant error triage"),
    ],
    sla=SLA(freshness_hours=3, quality_threshold=0.995, notify=["platform-observability"]),
    quality_checks=[check_paths_under_api],
)
def access_logs(partition_date: str) -> object:
    """Append one day of hourly request-log files."""
    return dlt.resource(
        read_access_logs(partition_date=partition_date).to_dict("records"),
        name="access_logs",
    )
