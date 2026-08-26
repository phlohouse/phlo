"""Generate the deterministic Delta portability fixture set.

The generator writes every byte the example consumes:

- ``telemetry/hour=<label>/readings.ndjson.gz``: one gzip-compressed NDJSON
  file per operating hour. Files deliberately contain verbatim
  retransmissions (duplicate ``message_id``) and, in the final hour,
  straggler events measured in earlier hours (late arrivals).
- ``telemetry/corrections/corrections.ndjson.gz``: late corrections that amend
  previously ingested readings by ``message_id``.
- ``evolved/readings_v2.csv``: the firmware-v2 batch for hour T06. It carries
  the additive ``signal_quality_dbm`` column that v1 batches never emit.
- ``regions/regions.csv``: replay of the PostgreSQL regions lookup that Sling
  full-refreshes into ``raw.delta_regions``.
- ``device_registry.sqlite``: the fleet registry database (devices and sites)
  that ingestion merges into reference assets.
- ``failures/``: labeled invalid fixtures; each breaks exactly one named
  invariant (physical bounds, sequence monotonicity, known devices, duplicate
  ratio, signal-quality bounds).

Every value derives from fixed arithmetic, so regenerating produces identical
files. Gzip members are written with ``mtime=0`` to keep archives stable.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "generated-data"

DAY = "2026-08-20"
OPERATING_HOURS = [f"{DAY}T{hour:02d}" for hour in range(6)]
EVOLVED_HOUR = f"{DAY}T06"
SLOTS_PER_HOUR = 6  # minutes 0..50 step 10

DEVICE_SITES = {
    "dev-001": ("site-north", "sensor-pro"),
    "dev-002": ("site-north", "sensor-lite"),
    "dev-003": ("site-north", "sensor-pro"),
    "dev-004": ("site-south", "sensor-lite"),
    "dev-005": ("site-south", "sensor-pro"),
    "dev-006": ("site-south", "sensor-lite"),
    "dev-007": ("site-east", "sensor-pro"),
    "dev-008": ("site-east", "sensor-lite"),
}
SITE_NAMES = {
    "site-north": ("North Cluster", "north"),
    "site-south": ("South Cluster", "south"),
    "site-east": ("East Cluster", "east"),
}
MODEL_FIRMWARE = {"sensor-pro": "fw-3.1", "sensor-lite": "fw-2.9"}

# Slot 5 of these device/hour pairs is withheld from its own hour file and
# delivered inside the final hour's file instead: late arrivals.
LATE_STRAGGLERS = {
    (f"{DAY}T01", "dev-002"),
    (f"{DAY}T02", "dev-004"),
    (f"{DAY}T03", "dev-006"),
    (f"{DAY}T04", "dev-008"),
}
LATE_DELIVERY_HOUR = OPERATING_HOURS[-1]

# One verbatim retransmission per hour file after the first: the first reading
# of the previous hour is appended again by the gateway.
RETRANSMITTED_DEVICE = "dev-001"

REGIONS = [
    ("north", "North Cluster", "NL", f"{DAY}T00:00:00"),
    ("south", "South Cluster", "PT", f"{DAY}T00:00:00"),
    ("east", "East Cluster", "PL", f"{DAY}T00:00:00"),
]


def _sequence(hour_index: int, slot: int) -> int:
    return hour_index * 100 + slot + 1


def _message_id(device_id: str, sequence: int) -> str:
    device_number = int(device_id.split("-")[1])
    return f"t-{device_number:03d}-{sequence:04d}"


def _reading(
    device_id: str,
    site_id: str,
    model: str,
    hour_label: str,
    ingested_from_hour: str,
    slot: int,
) -> dict[str, object]:
    device_number = int(device_id.split("-")[1])
    hour_index = int(hour_label.split("T")[1])
    sequence = _sequence(hour_index, slot)
    return {
        "message_id": _message_id(device_id, sequence),
        "device_id": device_id,
        "site_id": site_id,
        "sequence_number": sequence,
        "event_time": f"{hour_label}:{slot * 10:02d}:00",
        "event_hour": f"{hour_label}:00:00",
        "ingested_from_hour": f"{ingested_from_hour}:00:00",
        "temperature_c": round(16.0 + device_number * 0.7 + hour_index * 1.4 + slot * 0.2, 2),
        "humidity_pct": round(48.0 + (device_number % 3) * 6.0 - hour_index * 0.9, 2),
        "battery_pct": round(max(12.0, 96.0 - hour_index * 3.0 - device_number), 2),
        "firmware": MODEL_FIRMWARE[model],
        "rssi_dbm": -(48 + device_number * 2 + slot),
        "event_date": DAY,
    }


def build_readings() -> tuple[dict[str, list[dict[str, object]]], list[dict[str, object]]]:
    """Return per-hour delivery files and the late-straggler additions."""
    files: dict[str, list[dict[str, object]]] = {hour: [] for hour in OPERATING_HOURS}
    late_rows: list[dict[str, object]] = []
    for hour_index, hour in enumerate(OPERATING_HOURS):
        for device_id, (site_id, model) in DEVICE_SITES.items():
            for slot in range(SLOTS_PER_HOUR):
                row = _reading(device_id, site_id, model, hour, hour, slot)
                if (hour, device_id) in LATE_STRAGGLERS and slot == SLOTS_PER_HOUR - 1:
                    row["ingested_from_hour"] = f"{LATE_DELIVERY_HOUR}:00:00"
                    late_rows.append(row)
                    continue
                files[hour].append(row)

    # Gateway retransmission: the first reading of the previous hour is
    # appended again verbatim inside every later hour file.
    for hour_index in range(1, len(OPERATING_HOURS)):
        first = files[OPERATING_HOURS[hour_index - 1]][0]
        assert first["device_id"] == RETRANSMITTED_DEVICE, "retransmit anchor moved"
        files[OPERATING_HOURS[hour_index]].append(dict(first))

    files[LATE_DELIVERY_HOUR].extend(late_rows)
    return files, late_rows


def build_evolved() -> list[dict[str, object]]:
    """Firmware-v2 batch for the extra hour T06 with the additive column."""
    rows: list[dict[str, object]] = []
    hour_index = len(OPERATING_HOURS)
    for device_id, (site_id, model) in DEVICE_SITES.items():
        device_number = int(device_id.split("-")[1])
        for slot in range(SLOTS_PER_HOUR):
            base = _reading(device_id, site_id, model, EVOLVED_HOUR, EVOLVED_HOUR, slot)
            base["signal_quality_dbm"] = float(-(52 + device_number * 2 + slot))
            rows.append(base)
    assert hour_index == 6
    return rows


def build_corrections() -> list[dict[str, object]]:
    """Corrections amend existing readings by message id."""
    return [
        {
            "message_id": _message_id("dev-003", _sequence(0, 3)),
            "corrected_temperature_c": 24.5,
            "corrected_humidity_pct": None,
            "correction_reason": "calibration-offset",
            "corrected_at": f"{DAY}T06:15:00",
        },
        {
            "message_id": _message_id("dev-006", _sequence(3, 4)),
            "corrected_temperature_c": None,
            "corrected_humidity_pct": 61.0,
            "correction_reason": "drift-fix",
            "corrected_at": f"{DAY}T07:30:00",
        },
    ]


def _write_ndjson_gz(path: Path, rows: list[dict[str, object]]) -> None:
    payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(payload, mtime=0))


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_regions(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["region_code", "region_name", "country", "updated_at"])
        writer.writerows(REGIONS)


def _write_registry(path: Path) -> None:
    if path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE devices (
                device_id TEXT PRIMARY KEY,
                site_id TEXT NOT NULL,
                model TEXT NOT NULL,
                activated_at TEXT NOT NULL,
                decommissioned_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE sites (
                site_id TEXT PRIMARY KEY,
                site_name TEXT NOT NULL,
                region TEXT NOT NULL
            )
            """
        )
        for device_id, (site_id, model) in DEVICE_SITES.items():
            connection.execute(
                "INSERT INTO devices VALUES (?, ?, ?, ?, ?)",
                (device_id, site_id, model, "2025-03-01T08:00:00", None),
            )
        for site_id, (site_name, region) in SITE_NAMES.items():
            connection.execute(
                "INSERT INTO sites VALUES (?, ?, ?)",
                (site_id, site_name, region),
            )
        connection.commit()
    finally:
        connection.close()


def _failure_reading(
    message_id: str,
    device_id: str,
    hour_label: str,
    **overrides: object,
) -> dict[str, object]:
    site_id, model = DEVICE_SITES.get(device_id, ("site-north", "sensor-pro"))
    row = _reading(device_id, site_id, model, hour_label, hour_label, 0)
    row["message_id"] = message_id
    row.update(overrides)
    return row


def _write_failures(data: Path) -> None:
    failures = data / "failures"
    shutil.rmtree(failures, ignore_errors=True)
    failures.mkdir(parents=True)

    out_of_bounds = [
        _failure_reading("t-999-0001", "dev-001", OPERATING_HOURS[0], temperature_c=999.0),
    ]
    _write_ndjson_gz(failures / "readings_out_of_bounds.ndjson.gz", out_of_bounds)

    regression = [
        _failure_reading("t-004-0101", "dev-004", OPERATING_HOURS[1], sequence_number=105),
        _failure_reading("t-004-0102", "dev-004", OPERATING_HOURS[1], sequence_number=104),
    ]
    _write_ndjson_gz(failures / "readings_sequence_regression.ndjson.gz", regression)

    unknown_device = [
        _failure_reading("t-999-0001", "dev-999", OPERATING_HOURS[0]),
    ]
    _write_ndjson_gz(failures / "readings_unknown_device.ndjson.gz", unknown_device)

    burst_anchor = _failure_reading("t-001-0001", "dev-001", OPERATING_HOURS[0])
    duplicate_burst = [burst_anchor]
    for index in range(14):
        duplicate_burst.append(
            _failure_reading(
                f"t-050-{index:04d}",
                "dev-002",
                OPERATING_HOURS[0],
                temperature_c=round(20.0 + index, 2),
            )
        )
        duplicate_burst.append(
            dict(burst_anchor, event_time=f"{OPERATING_HOURS[0]}:{index:02d}:00")
        )
    _write_ndjson_gz(failures / "readings_duplicate_burst.ndjson.gz", duplicate_burst)

    signal_out_of_bounds = [
        dict(build_evolved()[0], message_id="t-099-0001", signal_quality_dbm=-9.0),
    ]
    _write_csv(failures / "evolved_signal_out_of_bounds.csv", signal_out_of_bounds)


def generate(data: Path = DEFAULT_DATA_DIR) -> dict[str, int]:
    """Regenerate every fixture under ``data`` and return summary counts."""
    shutil.rmtree(data / "telemetry", ignore_errors=True)
    shutil.rmtree(data / "evolved", ignore_errors=True)
    shutil.rmtree(data / "regions", ignore_errors=True)
    data.mkdir(parents=True, exist_ok=True)

    files, late_rows = build_readings()
    reading_rows = sum(len(rows) for rows in files.values())
    distinct_messages = {str(row["message_id"]) for rows in files.values() for row in rows}
    corrections = build_corrections()
    evolved = build_evolved()

    for hour, rows in files.items():
        _write_ndjson_gz(data / "telemetry" / f"hour={hour}" / "readings.ndjson.gz", rows)
    _write_ndjson_gz(data / "telemetry" / "corrections" / "corrections.ndjson.gz", corrections)
    _write_csv(data / "evolved" / "readings_v2.csv", evolved)
    _write_regions(data / "regions" / "regions.csv")
    _write_registry(data / "device_registry.sqlite")
    _write_failures(data)

    return {
        "reading_rows": reading_rows,
        "reading_messages": len(distinct_messages),
        "late_stragglers": len(late_rows),
        "corrections": len(corrections),
        "evolved_rows": len(evolved),
        "regions": len(REGIONS),
        "devices": len(DEVICE_SITES),
        "sites": len(SITE_NAMES),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    args = parser.parse_args()
    print(generate(args.data_dir))
