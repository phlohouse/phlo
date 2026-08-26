"""DLT ingestion of monthly ZIP/CSV weather bulk archives.

Each archive ``weather-<YYYY-MM>.zip`` holds one ``observations-<date>.csv``
member per observation day. Runs are daily partitions that resolve to their
calendar month: the partition key's ``YYYY-MM`` prefix selects exactly one
archive, so a month is fully re-readable from any day inside it.

Rows merge on ``observation_key``, a surrogate of the natural key
``(station_id, observed_at)``, keeping replays idempotent. The July 2026
archive adds ``pressure_hpa`` -
the contract declares it optional, so pre-drift months and the drift batch
validate under one schema (schema drift without contract churn).
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import dlt
import pandas as pd
import phlo
from phlo.contracts import SLA, Consumer

from workflows.schemas.contracts import ObservationSchema
from workflows.sources.weather_files.quality import assert_known_stations

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WEATHER_DIR = PROJECT_ROOT / "generated-data" / "weather"


def read_month_archive(month_label: str, weather_dir: Path = WEATHER_DIR) -> pd.DataFrame:
    """Read every CSV member of one monthly archive into a frame."""
    archive_path = weather_dir / f"weather-{month_label}.zip"
    if not archive_path.exists():
        raise FileNotFoundError(f"No weather archive for '{month_label}' under {weather_dir}")
    frames: list[pd.DataFrame] = []
    with zipfile.ZipFile(archive_path) as archive:
        for member in sorted(archive.namelist()):
            frame = pd.read_csv(
                io.BytesIO(archive.read(member)),
                dtype={"station_id": str},
                parse_dates=["observed_at"],
            )
            if not frame.empty:
                frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def read_observations(partition_date: str, weather_dir: Path = WEATHER_DIR) -> pd.DataFrame:
    """Read the archive for the partition's calendar month."""
    month_label = partition_date[:7]
    frame = read_month_archive(month_label, weather_dir)
    frame["obs_month"] = pd.Timestamp(f"{month_label}-01")
    frame["observation_key"] = frame["station_id"] + "|" + frame["observed_at"].astype(str)
    return frame


@phlo.ingest.dlt(
    table_name="weather_observations",
    unique_key="observation_key",
    validation_schema=ObservationSchema,
    group="weather_files",
    quality_checks=[assert_known_stations],
    freshness_hours=(744, 800),
    merge_strategy="merge",
    partition_spec=[("obs_month", "identity")],
    strict_validation=True,
    max_runtime_seconds=600,
    max_retries=2,
    retry_delay_seconds=120,
    add_metadata_columns=True,
    owner="climate-archives",
    consumers=[
        Consumer(name="research", usage="monthly climate indicators"),
        Consumer(name="operations", usage="station coverage monitoring"),
    ],
    sla=SLA(freshness_hours=800, quality_threshold=1.0),
)
def weather_observations(partition_date: str) -> object:
    """Merge one month of archived observations on the natural key."""
    return dlt.resource(
        read_observations(partition_date).to_dict("records"),
        name="weather_observations",
    )
