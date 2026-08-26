"""Generate the deterministic IoT telemetry fixture set.

The generator writes every byte the example consumes:

- ``telemetry/hour=<label>/readings.ndjson.gz``: one gzip-compressed NDJSON
  file per operating hour. Files deliberately contain verbatim
  retransmissions (duplicate ``message_id``) and, in the final hour,
  straggler events measured in earlier hours (late arrivals).
- ``telemetry/corrections/corrections.ndjson.gz``: late corrections that amend
  previously ingested readings by ``message_id``.
- ``device_registry.sqlite``: the fleet registry database (devices and sites)
  that ingestion merges into reference assets.
- ``failures/``: labeled invalid fixtures; each breaks exactly one named
  invariant (physical bounds, sequence monotonicity, known devices, duplicate
  ratio, file-count pressure).

Every value derives from fixed arithmetic, so regenerating produces identical
files. Gzip members are written with ``mtime=0`` to keep archives stable.
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "generated-data"

DAY = "2026-08-20"
OPERATING_HOURS = [f"{DAY}T{hour:02d}" for hour in range(6)]
CORRECTION_HOUR = f"{DAY}T06"
SLOTS_PER_HOUR = 11  # minutes 0..50 step 5; slot 11 is held back and arrives late

DEVICE_SITES = {
    "dev-001": ("site-alpha", "sensor-xl"),
    "dev-002": ("site-alpha", "sensor-lite"),
    "dev-003": ("site-alpha", "sensor-xl"),
    "dev-004": ("site-beta", "sensor-lite"),
    "dev-005": ("site-beta", "sensor-xl"),
    "dev-006": ("site-beta", "sensor-lite"),
    "dev-007": ("site-gamma", "sensor-xl"),
    "dev-008": ("site-gamma", "sensor-lite"),
}
SITE_NAMES = {
    "site-alpha": ("Alpha Assembly Hall", "north"),
    "site-beta": ("Beta Logistics Yard", "south"),
    "site-gamma": ("Gamma Cold Store", "north"),
}

# Slot 11 of these device/hour pairs is withheld from its own hour file and
# delivered inside the final hour's file instead: late arrivals.
LATE_STRAGGLERS = {
    (f"{DAY}T01", "dev-002"),
    (f"{DAY}T02", "dev-005"),
    (f"{DAY}T03", "dev-008"),
    (f"{DAY}T04", "dev-003"),
}
LATE_DELIVERY_HOUR = OPERATING_HOURS[-1]

# One verbatim retransmission per hour file after the first: the first reading
# of the previous hour is appended again by the gateway.
RETRANSMITTED_DEVICE = "dev-001"

MAX_DUPLICATE_RATIO = 0.02
MAX_FILES_PER_HOUR = 24


def _sequence(hour_index: int, slot: int) -> int:
    return hour_index * 100 + slot + 1


def _message_id(device_id: str, sequence: int) -> str:
    device_number = int(device_id.split("-")[1])
    return f"m-{device_number:03d}-{sequence:04d}"


def _reading(
    device_id: str,
    site_id: str,
    sequence: int,
    event_hour: str,
    ingested_from_hour: str,
    slot: int,
) -> dict[str, object]:
    device_number = int(device_id.split("-")[1])
    base_temperature = 21.0 + device_number + (slot % 4) * 1.5
    return {
        "message_id": _message_id(device_id, sequence),
        "device_id": device_id,
        "site_id": site_id,
        "sequence_number": sequence,
        "event_time": f"{event_hour}:{slot * 5:02d}:00Z",
        "event_hour": event_hour,
        "ingested_from_hour": ingested_from_hour,
        "temperature_c": round(base_temperature + ((device_number + slot) % 3) * 0.4, 2),
        "humidity_pct": float(40 + ((slot * 7 + device_number * 3) % 35)),
        "battery_pct": round(96.0 - hour_of(event_hour) - slot * 0.15, 2),
        "firmware": "2.5.0" if device_number % 2 == 0 else "2.4.1",
        "rssi_dbm": -(48 + ((slot * 3 + device_number * 7) % 42)),
    }


def hour_of(event_hour: str) -> int:
    """Return the zero-padded hour component of an ``YYYY-MM-DDTHH`` label."""
    return int(event_hour.split("T")[1])


def build_readings() -> tuple[
    dict[str, list[dict[str, object]]], dict[str, list[dict[str, object]]]
]:
    """Return per-hour delivery files and the late-straggler additions."""
    files: dict[str, list[dict[str, object]]] = {hour: [] for hour in OPERATING_HOURS}
    late_rows: dict[str, list[dict[str, object]]] = {}
    for hour_index, hour in enumerate(OPERATING_HOURS):
        for device_id, (site_id, _model) in DEVICE_SITES.items():
            for slot in range(SLOTS_PER_HOUR):
                if slot == SLOTS_PER_HOUR - 1 and (hour, device_id) in LATE_STRAGGLERS:
                    continue  # withheld from its own hour; delivered late below
                sequence = _sequence(hour_index, slot)
                files[hour].append(_reading(device_id, site_id, sequence, hour, hour, slot))
        if hour_index > 0:
            previous_first = files[OPERATING_HOURS[hour_index - 1]][0]
            files[hour].append(dict(previous_first))
    for event_hour, device_id in sorted(LATE_STRAGGLERS):
        hour_index = OPERATING_HOURS.index(event_hour)
        site_id, _model = DEVICE_SITES[device_id]
        row = _reading(
            device_id,
            site_id,
            _sequence(hour_index, SLOTS_PER_HOUR - 1),
            event_hour,
            LATE_DELIVERY_HOUR,
            SLOTS_PER_HOUR - 1,
        )
        files[LATE_DELIVERY_HOUR].append(row)
        late_rows.setdefault(LATE_DELIVERY_HOUR, []).append(row)
    return files, late_rows


def build_corrections() -> list[dict[str, object]]:
    """Corrections amend existing readings by message id."""
    return [
        {
            "message_id": _message_id("dev-003", _sequence(0, 3)),
            "corrected_temperature_c": 24.5,
            "corrected_humidity_pct": None,
            "correction_reason": "calibration_offset",
            "corrected_at": f"{CORRECTION_HOUR}:05:00Z",
        },
        {
            "message_id": _message_id("dev-006", _sequence(3, 6)),
            "corrected_temperature_c": None,
            "corrected_humidity_pct": 61.0,
            "correction_reason": "sensor_drift",
            "corrected_at": f"{CORRECTION_HOUR}:10:00Z",
        },
    ]


def _write_ndjson_gz(path: Path, rows: list[dict[str, object]]) -> None:
    payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(payload, mtime=0))


def _write_registry(path: Path) -> None:
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE devices (
                device_id TEXT PRIMARY KEY,
                site_id TEXT NOT NULL,
                model TEXT NOT NULL,
                activated_at TEXT NOT NULL,
                decommissioned_at TEXT
            );
            CREATE TABLE sites (
                site_id TEXT PRIMARY KEY,
                site_name TEXT NOT NULL,
                region TEXT NOT NULL
            );
            """
        )
        for index, (device_id, (site_id, model)) in enumerate(sorted(DEVICE_SITES.items())):
            connection.execute(
                "INSERT INTO devices VALUES (?, ?, ?, ?, NULL)",
                (device_id, site_id, model, f"2025-09-{index + 1:02d}T09:00:00Z"),
            )
        for site_id, (site_name, region) in sorted(SITE_NAMES.items()):
            connection.execute(
                "INSERT INTO sites VALUES (?, ?, ?)",
                (site_id, site_name, region),
            )
        connection.commit()
    finally:
        connection.close()


def _write_failures(data: Path) -> None:
    failures = data / "failures"
    failures.mkdir()
    _write_ndjson_gz(
        failures / "readings_out_of_bounds.ndjson.gz",
        [
            _failure_reading("fb-oob-1", temperature_c=999.0),
            _failure_reading("fb-oob-2", humidity_pct=140.0),
        ],
    )
    regression = [
        _failure_reading("fb-seq-a", device_id="dev-003", sequence_number=502),
        _failure_reading("fb-seq-b", device_id="dev-003", sequence_number=401),
    ]
    regression[1]["event_time"] = f"{DAY}T05:55:00Z"
    regression[0]["event_time"] = f"{DAY}T05:50:00Z"
    _write_ndjson_gz(failures / "readings_sequence_regression.ndjson.gz", regression)
    _write_ndjson_gz(
        failures / "readings_unknown_device.ndjson.gz",
        [
            _failure_reading("fb-dev-a", device_id="dev-999"),
            _failure_reading("fb-dev-b", device_id="dev-999"),
        ],
    )
    burst = [_failure_reading("fb-dup", device_id="dev-004") for _ in range(12)]
    burst += [_failure_reading(f"fb-ok-{index}", device_id="dev-004") for index in range(8)]
    _write_ndjson_gz(failures / "readings_duplicate_burst.ndjson.gz", burst)
    pressure = failures / "pressure" / f"hour={CORRECTION_HOUR}"
    for index in range(40):
        _write_ndjson_gz(
            pressure / f"chunk-{index:02d}.ndjson.gz",
            [
                _failure_reading(f"fb-pressure-{index}-{row}", device_id="dev-007")
                for row in range(2)
            ],
        )


def _failure_reading(
    message_id: str,
    device_id: str = "dev-001",
    sequence_number: int = 900,
    temperature_c: float = 23.0,
    humidity_pct: float = 50.0,
) -> dict[str, object]:
    site_id, _model = DEVICE_SITES.get(device_id, ("site-alpha", "sensor-xl"))
    return {
        "message_id": message_id,
        "device_id": device_id,
        "site_id": site_id,
        "sequence_number": sequence_number,
        "event_time": f"{DAY}T05:59:00Z",
        "event_hour": f"{DAY}T05",
        "ingested_from_hour": f"{DAY}T05",
        "temperature_c": temperature_c,
        "humidity_pct": humidity_pct,
        "battery_pct": 71.0,
        "firmware": "2.5.0",
        "rssi_dbm": -60,
    }


def generate(data: Path = DEFAULT_DATA_DIR) -> dict[str, int]:
    """Regenerate every fixture under ``data`` and return summary counts."""
    if data.exists():
        shutil.rmtree(data)
    data.mkdir(parents=True)
    files, late_rows = build_readings()
    for hour, rows in sorted(files.items()):
        _write_ndjson_gz(data / "telemetry" / f"hour={hour}" / "readings.ndjson.gz", rows)
    _write_ndjson_gz(
        data / "telemetry" / "corrections" / "corrections.ndjson.gz",
        build_corrections(),
    )
    _write_registry(data / "device_registry.sqlite")
    _write_failures(data)
    readings = sum(len(rows) for rows in files.values())
    duplicates = max(0, len(OPERATING_HOURS) - 1)
    return {
        "hours": len(OPERATING_HOURS),
        "readings": readings,
        "distinct_readings": readings - duplicates,
        "duplicates": duplicates,
        "late": sum(len(rows) for rows in late_rows.values()),
        "corrections": len(build_corrections()),
        "devices": len(DEVICE_SITES),
        "sites": len(SITE_NAMES),
        "max_files_per_hour": MAX_FILES_PER_HOUR,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    args = parser.parse_args()
    print(generate(args.data_dir))
