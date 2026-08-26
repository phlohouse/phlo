"""Generate the deterministic WAP failure-lab fixture set.

Every scenario batch is computed from fixed formulas - no randomness, no wall
clock - so regeneration is byte-stable. Files land under
``generated-data/scenarios/<scenario>/`` and are named
``<flavor>-<partition-date>.ndjson.gz``. The pipeline itself only ever reads
``generated-data/inbound/``; this script stages the valid_publish batch there
as the safe default, while ``scripts/run_scenario.py`` restages per scenario.

Labeled failure fixtures each break exactly one invariant:

- ``batches_null_reading``: one null ``reading_value`` (Pandera not-null).
- ``batches_duplicate_batch_id``: one repeated ``batch_id`` (uniqueness check).
- ``batches_stale``: recordings 23 days before their partition (staleness).
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "generated-data"

PARTITION_A = "2026-08-20"
PARTITION_B = "2026-08-21"
RETRY_PARTITION = "2026-08-22"
SCHEMA_PARTITION = "2026-08-23"
WARNING_PARTITION = "2026-08-24"

VALID_ROWS = 12
NULL_ROWS = 6
DUPLICATE_ROWS = 6
RETRY_ROWS = 10
SCHEMA_ROWS = 8
CONCURRENT_A_ROWS = 12
CONCURRENT_B_ROWS = 8
STALE_ROWS = 7

DUPLICATED_BATCH_ID = "b-2003"
NULL_ROW_INDEX = 3
STALE_RECORDING_DAY = "2026-08-01"


def _sensor(index: int) -> str:
    return f"s-{index:03d}"


def _batch(index: int) -> str:
    return f"b-{index:04d}"


def _row(
    batch_index: int,
    sensor_index: int,
    partition: str,
    *,
    reading_value: float | None,
    recorded_day: str | None = None,
    reading_quality_score: float | None = None,
) -> dict[str, object]:
    """Build one deterministic batch row."""
    slot = batch_index % 24
    row: dict[str, object] = {
        "batch_id": _batch(batch_index),
        "sensor_id": _sensor(sensor_index),
        "reading_value": reading_value,
        "recorded_at": (
            f"{recorded_day or partition}T{6 + slot // 4:02d}:{(slot * 13) % 60:02d}:00Z"
        ),
        "batch_date": f"{partition}T00:00:00Z",
        "quality_flag": "suspect" if batch_index % 5 == 0 else "ok",
    }
    if reading_quality_score is not None:
        row["reading_quality_score"] = reading_quality_score
    return row


def _clean_rows(
    first_batch: int,
    first_sensor: int,
    count: int,
    partition: str,
    *,
    sensors: int,
) -> list[dict[str, object]]:
    rows = []
    for offset in range(count):
        batch_index = first_batch + offset
        sensor_index = first_sensor + (offset % sensors)
        value = round(18.0 + ((offset * 7) % 45) / 2 + (offset % sensors), 2)
        rows.append(_row(batch_index, sensor_index, partition, reading_value=value))
    return rows


def build_valid_publish() -> list[dict[str, object]]:
    """Clean baseline: 12 rows across four sensors on one partition."""
    return _clean_rows(1, 1, VALID_ROWS, PARTITION_A, sensors=4)


def build_null_reading() -> list[dict[str, object]]:
    """Six clean-shaped rows except one null reading_value (invariant: not-null)."""
    rows = _clean_rows(1001, 501, NULL_ROWS, PARTITION_A, sensors=3)
    rows[NULL_ROW_INDEX]["reading_value"] = None
    return rows


def build_duplicate_batch_id() -> list[dict[str, object]]:
    """Seven rows where batch id b-2003 appears twice (invariant: uniqueness)."""
    rows = _clean_rows(2001, 511, DUPLICATE_ROWS + 1, PARTITION_A, sensors=3)
    replay = dict(rows[2])
    replay["recorded_at"] = f"{PARTITION_A}T23:{(DUPLICATE_ROWS * 7) % 60:02d}:00Z"
    rows.append(replay)
    return rows


def build_retry_recovery() -> list[dict[str, object]]:
    """Clean retry-scenario batch; the transient failure is injected at runtime."""
    return _clean_rows(3001, 301, RETRY_ROWS, RETRY_PARTITION, sensors=5)


def build_schema_change() -> list[dict[str, object]]:
    """Clean batch carrying the new optional reading_quality_score column."""
    base = _clean_rows(4001, 401, SCHEMA_ROWS, SCHEMA_PARTITION, sensors=4)
    return [
        {**row, "reading_quality_score": round(55.0 + index * 5, 1)}
        for index, row in enumerate(base)
    ]


def build_concurrent_partition_a() -> list[dict[str, object]]:
    """First concurrent partition: disjoint sensors and batch ids."""
    return _clean_rows(6001, 101, CONCURRENT_A_ROWS, PARTITION_A, sensors=4)


def build_concurrent_partition_b() -> list[dict[str, object]]:
    """Second concurrent partition: no id overlap with partition A."""
    return _clean_rows(7001, 201, CONCURRENT_B_ROWS, PARTITION_B, sensors=4)


def build_stale_batches() -> list[dict[str, object]]:
    """Recordings 23 days before their partition (invariant: staleness window)."""
    rows = []
    for offset in range(STALE_ROWS):
        batch_index = 8001 + offset
        sensor_index = 801 + (offset % 3)
        value = round(15.0 + ((offset * 11) % 30) / 2 + (offset % 3), 2)
        rows.append(
            _row(
                batch_index,
                sensor_index,
                WARNING_PARTITION,
                reading_value=value,
                recorded_day=STALE_RECORDING_DAY,
            )
        )
    return rows


def _write_ndjson_gz(path: Path, rows: list[dict[str, object]]) -> None:
    payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(payload, mtime=0))


def _stage_inbound(scenarios_dir: Path, inbound_dir: Path) -> int:
    """Reset the pipeline-visible inbound directory to the valid_publish batch."""
    if inbound_dir.exists():
        shutil.rmtree(inbound_dir)
    inbound_dir.mkdir(parents=True)
    source = scenarios_dir / "valid_publish" / f"batches-{PARTITION_A}.ndjson.gz"
    target = inbound_dir / source.name
    shutil.copyfile(source, target)
    return 1


def generate(data: Path = DEFAULT_DATA_DIR) -> dict[str, int]:
    """Regenerate every fixture under ``data`` and return summary counts."""
    scenarios = data / "scenarios"
    fixtures: list[tuple[str, str, list[dict[str, object]]]] = [
        ("valid_publish", f"batches-{PARTITION_A}.ndjson.gz", build_valid_publish()),
        (
            "quality_failure",
            f"batches_null_reading-{PARTITION_A}.ndjson.gz",
            build_null_reading(),
        ),
        (
            "quality_failure",
            f"batches_duplicate_batch_id-{PARTITION_A}.ndjson.gz",
            build_duplicate_batch_id(),
        ),
        ("retry_recovery", f"batches-{RETRY_PARTITION}.ndjson.gz", build_retry_recovery()),
        ("schema_change", f"batches-{SCHEMA_PARTITION}.ndjson.gz", build_schema_change()),
        (
            "concurrent_runs",
            f"partition_a-{PARTITION_A}.ndjson.gz",
            build_concurrent_partition_a(),
        ),
        (
            "concurrent_runs",
            f"partition_b-{PARTITION_B}.ndjson.gz",
            build_concurrent_partition_b(),
        ),
        ("warning_only", f"batches_stale-{WARNING_PARTITION}.ndjson.gz", build_stale_batches()),
    ]
    for scenario, filename, rows in fixtures:
        _write_ndjson_gz(scenarios / scenario / filename, rows)
    staged = _stage_inbound(scenarios, data / "inbound")
    return {
        "valid_publish": VALID_ROWS,
        "null_reading": NULL_ROWS,
        "duplicate_batch_id": DUPLICATE_ROWS + 1,
        "retry_recovery": RETRY_ROWS,
        "schema_change": SCHEMA_ROWS,
        "concurrent_a": CONCURRENT_A_ROWS,
        "concurrent_b": CONCURRENT_B_ROWS,
        "stale": STALE_ROWS,
        "inbound_files": staged,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    args = parser.parse_args()
    print(generate(args.data_dir))
