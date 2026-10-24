"""Generate the preset's deterministic source pull.

The preset ships no raw data. This script writes the files a source client
would have fetched:

* ``data/raw/readings.json`` - hourly measurements from three stations;
* ``data/notes/<month>.md`` - the network's monthly published notes;
* ``data/raw/manifest.json`` - counts, expected aggregates and the figures
  that were deliberately altered in the notes.

The same seed always writes the same bytes. The manifest is the proof's
independent ground truth: expected means are computed here, in Python, while
the lakehouse computes them in SQL.

A few published figures are altered on purpose so the report has something
real to reconcile. ``injected_errors`` names them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from source_to_report import paths
from source_to_report.domain import POLLUTANTS, STATIONS, YEAR

SEED = 20250101
MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

# Base concentration per station and pollutant, in µg/m³.
BASE = {
    "riverside": {"PM2.5": 8.2, "NO2": 29.0},
    "harbour": {"PM2.5": 6.7, "NO2": 18.5},
    "northgate": {"PM2.5": 9.9, "NO2": 35.0},
}

# Winter pollution episodes: (first day, last day, multiplier for PM2.5).
EPISODES = (
    (datetime(YEAR, 1, 17, tzinfo=UTC), datetime(YEAR, 1, 20, tzinfo=UTC), 1.85),
    (datetime(YEAR, 2, 8, tzinfo=UTC), datetime(YEAR, 2, 10, tzinfo=UTC), 1.55),
    (datetime(YEAR, 11, 12, tzinfo=UTC), datetime(YEAR, 11, 15, tzinfo=UTC), 1.95),
    (datetime(YEAR, 12, 3, tzinfo=UTC), datetime(YEAR, 12, 6, tzinfo=UTC), 1.75),
)

# (month index, station id, pollutant id, amount added to the true mean).
INJECTED_ERRORS = (
    (3, "harbour", "PM2.5", 0.6),
    (7, "northgate", "NO2", -1.2),
    (10, "riverside", "PM2.5", 0.5),
)


def _bell(hour: int, centre: float, width: float) -> float:
    """A smooth peak centred on ``centre``."""
    return math.exp(-0.5 * ((hour - centre) / width) ** 2)


def _seasonal(day_of_year: int, amplitude: float) -> float:
    """Winter-high, summer-low cycle peaking around 10 January."""
    return 1.0 + amplitude * math.cos(2.0 * math.pi * (day_of_year - 10) / 365.0)


def _diurnal(hour: int, pollutant: str) -> float:
    """Morning and evening traffic peaks, stronger for NO2."""
    if pollutant == "NO2":
        return (
            1.0
            + 0.30 * _bell(hour, 8, 2.2)
            + 0.24 * _bell(hour, 18, 2.6)
            - 0.14 * _bell(hour, 3, 3.0)
        )
    return (
        1.0 + 0.10 * _bell(hour, 8, 2.0) + 0.08 * _bell(hour, 19, 2.5) - 0.05 * _bell(hour, 4, 3.0)
    )


def _episode(moment: datetime, pollutant: str) -> float:
    """Return the episode multiplier for a moment, if one is running."""
    for start, end, multiplier in EPISODES:
        if start <= moment <= end + timedelta(hours=23):
            excess = multiplier - 1.0
            return 1.0 + (excess if pollutant == "PM2.5" else 0.7 * excess)
    return 1.0


def _readings(seed: int) -> list[dict[str, object]]:
    """Build every hourly reading in a stable order."""
    rng = random.Random(seed)
    rows: list[dict[str, object]] = []
    moment = datetime(YEAR, 1, 1, tzinfo=UTC)
    end = datetime(YEAR, 12, 31, 23, tzinfo=UTC)
    hours = int((end - moment).total_seconds() // 3600) + 1

    for station in STATIONS:
        for pollutant in POLLUTANTS:
            for offset in range(hours):
                observed = moment + timedelta(hours=offset)
                weekend = observed.weekday() >= 5
                weekday_factor = (0.72 if pollutant.id == "NO2" else 0.90) if weekend else 1.0
                noise = min(1.6, max(0.65, rng.lognormvariate(0.0, 0.14)))
                value = (
                    BASE[station.id][pollutant.id]
                    * _seasonal(
                        observed.timetuple().tm_yday, 0.30 if pollutant.id == "PM2.5" else 0.34
                    )
                    * _diurnal(observed.hour, pollutant.id)
                    * weekday_factor
                    * _episode(observed, pollutant.id)
                    * noise
                )
                rows.append(
                    {
                        "reading_id": f"{station.id}-{pollutant.slug}-{observed:%Y%m%d%H}",
                        "station_id": station.id,
                        "station_name": station.name,
                        "pollutant": pollutant.id,
                        "observed_at": observed.strftime("%Y-%m-%d %H:%M:%S"),
                        "observed_date": observed.strftime("%Y-%m-%d"),
                        "value": max(0.4, round(value, 1)),
                        "unit": pollutant.unit,
                        "source": "reference_monitor",
                    }
                )
    return rows


def _monthly_means(rows: list[dict[str, object]]) -> dict[str, dict[str, dict[str, float]]]:
    """Mean concentration per month, station and pollutant."""
    totals: dict[tuple[str, str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for row in rows:
        key = (str(row["observed_date"])[:7], str(row["station_id"]), str(row["pollutant"]))
        bucket = totals[key]
        bucket[0] += float(row["value"])
        bucket[1] += 1.0

    means: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    for (month, station_id, pollutant), (total, count) in totals.items():
        means[month].setdefault(station_id, {})[pollutant] = round(total / count, 6)
    return {month: dict(stations) for month, stations in means.items()}


def _annual_means(rows: list[dict[str, object]]) -> dict[str, dict[str, float]]:
    """Mean concentration for the year, per station and pollutant."""
    totals: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for row in rows:
        key = (str(row["station_id"]), str(row["pollutant"]))
        bucket = totals[key]
        bucket[0] += float(row["value"])
        bucket[1] += 1.0
    result: dict[str, dict[str, float]] = defaultdict(dict)
    for (station_id, pollutant), (total, count) in totals.items():
        result[station_id][pollutant] = round(total / count, 6)
    return dict(result)


def _error_lookup() -> dict[tuple[str, str, str], float]:
    """Map (month, station, pollutant) to the amount added to the published figure."""
    return {
        (f"{YEAR}-{month:02d}", station_id, pollutant): delta
        for month, station_id, pollutant, delta in INJECTED_ERRORS
    }


def _notes(means: dict[str, dict[str, dict[str, float]]]) -> dict[str, str]:
    """Render one published note per month, with the injected errors applied."""
    errors = _error_lookup()
    notes: dict[str, str] = {}
    for month_number, name in enumerate(MONTH_NAMES, start=1):
        month = f"{YEAR}-{month_number:02d}"
        lines = [
            f"# Monitoring note — {name} {YEAR}",
            "",
            "Monthly mean concentrations from the reference monitoring network.",
            "",
        ]
        for pollutant in POLLUTANTS:
            figures = []
            for station in STATIONS:
                measured = means[month][station.id][pollutant.id]
                delta = errors.get((month, station.id, pollutant.id), 0.0)
                stated = round(round(measured, 1) + delta, 1)
                figures.append(f"{station.name} {stated:.1f} {pollutant.unit}")
            lines.append(f"{pollutant.id} monthly mean: {', '.join(figures)}.")
            lines.append("")
        notes[f"{month}.md"] = "\n".join(lines).rstrip() + "\n"
    return notes


def _write_json(path: Path, value: object, *, compact: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    else:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")


def generate(data_dir: Path, seed: int = SEED) -> dict[str, object]:
    """Write the pull, notes and manifest under ``data_dir``. Return the manifest."""
    rows = _readings(seed)
    means = _monthly_means(rows)
    annual = _annual_means(rows)
    notes = _notes(means)

    notes_dir = data_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    for stale in notes_dir.glob("*.md"):
        if stale.name not in notes:
            stale.unlink()
    note_records = []
    for name, text in sorted(notes.items()):
        path = notes_dir / name
        path.write_text(text, encoding="utf-8")
        note_records.append(
            {
                "path": f"notes/{name}",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "claims": len(STATIONS) * len(POLLUTANTS),
            }
        )

    errors = _error_lookup()
    injected = []
    for (month, station_id, pollutant), delta in sorted(errors.items()):
        measured = means[month][station_id][pollutant]
        injected.append(
            {
                "month": month,
                "station_id": station_id,
                "pollutant": pollutant,
                "measured": round(measured, 6),
                "published": round(round(measured, 1) + delta, 1),
                "delta": delta,
            }
        )

    manifest: dict[str, object] = {
        "generated_by": "scripts/generate_fixtures.py",
        "seed": seed,
        "period": {
            "start": f"{YEAR}-01-01T00:00:00Z",
            "end": f"{YEAR}-12-31T23:00:00Z",
        },
        "readings": len(rows),
        "readings_by_station": {
            station.id: {
                pollutant.id: sum(
                    1
                    for row in rows
                    if row["station_id"] == station.id and row["pollutant"] == pollutant.id
                )
                for pollutant in POLLUTANTS
            }
            for station in STATIONS
        },
        "notes": note_records,
        "claims": sum(record["claims"] for record in note_records),
        "annual_means": annual,
        "monthly_means": {month: dict(stations) for month, stations in sorted(means.items())},
        "injected_errors": injected,
    }

    _write_json(data_dir / "raw" / "readings.json", rows, compact=True)
    _write_json(data_dir / "raw" / "manifest.json", manifest, compact=False)
    return manifest


def main(argv: list[str] | None = None) -> int:
    """Write the deterministic pull and print what was written."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=paths.data_dir(),
        help="where to write readings.json, manifest.json and notes/",
    )
    parser.add_argument("--seed", type=int, default=SEED, help="random seed (default: %(default)s)")
    args = parser.parse_args(argv)

    manifest = generate(args.data_dir, args.seed)
    print(f"readings   {manifest['readings']:,} -> {args.data_dir / 'raw' / 'readings.json'}")
    print(f"notes      {len(manifest['notes'])} -> {args.data_dir / 'notes'}")
    print(f"claims     {manifest['claims']}")
    print(f"manifest   -> {args.data_dir / 'raw' / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
