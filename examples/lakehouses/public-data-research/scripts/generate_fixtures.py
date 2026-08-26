"""Generate deterministic public-data research fixtures.

Every byte the example consumes derives from fixed arithmetic:

- ``api/place-registry-<date>.json``: paginated civic place registry payloads
  shaped like the REST feed the replay server serves. The baseline date
  carries five places; a later revision page restates one place with an
  updated population (upstream revision).
- ``civic/places.geojson``: polygon metadata per place; the ingestion asset
  computes centroid lat/lon and flattens properties.
- ``weather/weather-<YYYY-MM>.zip``: monthly bulk archives holding one
  ``observations-<date>.csv`` member per observation day. July adds a
  ``pressure_hpa`` column (schema drift); Fahrenheit-flagged rows store exact
  integer conversions so normalization stays lossless.
- ``demographics/demographics-<year>.csv``: annual regional population and
  median-age files.
- ``failures/``: labeled invalid batches; each breaks exactly one named
  invariant (orphan weather station, negative precipitation).

Temperatures and precipitation follow modular arithmetic over fixed station,
day, and hour indices, so monthly indicators and rollup reconciliations are
exactly reproducible.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "generated-data"

REGISTRY_BASELINE_DATE = "2026-08-10"
REGISTRY_REVISION_DATE = "2026-08-11"
PAGE_SIZE = 3

# place_id, name, region, lat, lon, population_year, population
BASELINE_PLACES = [
    ("P1", "Northfield", "north", 40.10, -88.20, 2025, 81000),
    ("P2", "Riverbend", "south", 39.80, -89.50, 2025, 45500),
    ("P3", "Lakeside", "north", 40.55, -88.95, 2025, 120000),
    ("P4", "Hillcrest", "south", 39.35, -90.10, 2025, 30800),
    ("P5", "Fairfield", "north", 40.75, -89.30, 2025, 66200),
]

REVISED_PLACE = "P3"
REVISED_POPULATION = 121500

WEATHER_STATIONS = ["P1", "P2", "P3", "P4"]
ORPHAN_STATION = "PX"
WEATHER_MONTHS = ["2026-05", "2026-06", "2026-07"]
DRIFT_MONTH = "2026-07"  # first archive carrying pressure_hpa (schema drift)
OBSERVATION_DAYS = ["01", "02", "03"]
OBSERVATION_HOURS = ["00", "12"]

# Fahrenheit-flagged rows store exact integer conversions: F -> C is lossless.
F_TO_C = [(50.0, 10.0), (59.0, 15.0), (68.0, 20.0), (77.0, 25.0)]

DEMOGRAPHIC_YEARS = [2025, 2026]
REGION_DEMOGRAPHICS = {
    ("north", 2025): (1200000, 38.4),
    ("north", 2026): (1214000, 38.9),
    ("south", 2025): (950000, 41.2),
    ("south", 2026): (961500, 41.6),
}

CLASSIFICATIONS = ["city", "town", "city", "village", "town"]

OBSERVATION_COLUMNS = ["station_id", "observed_at", "temp_c", "precip_mm", "unit_f"]
DRIFT_COLUMNS = [*OBSERVATION_COLUMNS, "pressure_hpa"]


def place_records() -> list[dict[str, object]]:
    """Baseline registry rows served on the baseline date."""
    return [
        {
            "place_id": place_id,
            "name": name,
            "region": region,
            "lat": lat,
            "lon": lon,
            "population_year": population_year,
            "population": population,
        }
        for place_id, name, region, lat, lon, population_year, population in BASELINE_PLACES
    ]


def revised_place_record() -> dict[str, object]:
    """The revision page restates one place with an updated population."""
    record = next(r for r in place_records() if r["place_id"] == REVISED_PLACE)
    return {**record, "population": REVISED_POPULATION}


def paginate(rows: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    return [rows[i : i + PAGE_SIZE] for i in range(0, len(rows), PAGE_SIZE)]


def build_registry_payloads() -> dict[str, dict[str, object]]:
    return {
        REGISTRY_BASELINE_DATE: {
            "registry_date": REGISTRY_BASELINE_DATE,
            "pages": paginate(place_records()),
        },
        REGISTRY_REVISION_DATE: {
            "registry_date": REGISTRY_REVISION_DATE,
            "pages": paginate([revised_place_record()]),
        },
    }


def geojson_features() -> list[dict[str, object]]:
    features = []
    for index, record in enumerate(place_records()):
        lat = float(record["lat"]) + 0.01  # polygon center sits off the registry point
        lon = float(record["lon"]) - 0.02
        half = 0.05
        ring = [
            [round(lon - half, 6), round(lat - half, 6)],
            [round(lon + half, 6), round(lat - half, 6)],
            [round(lon + half, 6), round(lat + half, 6)],
            [round(lon - half, 6), round(lat + half, 6)],
            [round(lon - half, 6), round(lat - half, 6)],  # closed ring repeats the first vertex
        ]
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "place_id": record["place_id"],
                    "region_code": f"{record['region']}-{record['place_id']}",
                    "elevation_m": 150.0 + index * 17.0,
                    "classification": CLASSIFICATIONS[index],
                },
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        )
    return features


def build_places_geojson() -> dict[str, object]:
    return {"type": "FeatureCollection", "features": geojson_features()}


def observation_rows(month_label: str) -> list[dict[str, object]]:
    """One month of observations; the drift month adds pressure_hpa."""
    drift = month_label == DRIFT_MONTH
    rows: list[dict[str, object]] = []
    for station_idx, station in enumerate(WEATHER_STATIONS):
        for day_idx, day in enumerate(OBSERVATION_DAYS):
            for hour_idx, hour in enumerate(OBSERVATION_HOURS):
                observed_at = f"{month_label}-{day}T{hour}:00:00Z"
                fahrenheit = station_idx % 2 == 0 and hour_idx == 1
                if fahrenheit:
                    temp_value = F_TO_C[(day_idx + station_idx) % len(F_TO_C)][0]
                else:
                    temp_value = float(8 + (day_idx * 3 + hour_idx * 5 + station_idx) % 13)
                row: dict[str, object] = {
                    "station_id": station,
                    "observed_at": observed_at,
                    "temp_c": temp_value,
                    "precip_mm": float((station_idx * 2 + day_idx + hour_idx) % 4),
                    "unit_f": fahrenheit,
                }
                if drift:
                    row["pressure_hpa"] = 1012.0 + station_idx + hour_idx * 0.5
                rows.append(row)
    return rows


def observations_csv(rows: list[dict[str, object]]) -> str:
    columns = DRIFT_COLUMNS if any("pressure_hpa" in row for row in rows) else OBSERVATION_COLUMNS
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        rendered: dict[str, object] = {
            "station_id": row["station_id"],
            "observed_at": row["observed_at"],
            "temp_c": f"{row['temp_c']:.1f}",
            "precip_mm": f"{row['precip_mm']:.1f}",
            "unit_f": "true" if row["unit_f"] else "false",
        }
        if "pressure_hpa" in columns:
            rendered["pressure_hpa"] = f"{row['pressure_hpa']:.1f}" if "pressure_hpa" in row else ""
        writer.writerow(rendered)
    return buffer.getvalue()


def write_zip(path: Path, members: dict[str, str]) -> None:
    """Write a byte-stable archive: fixed timestamps, ordering, and level."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, members[name])


def demographics_csv(year: int) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["year", "region", "population", "median_age"])
    for (region, demographic_year), (population, median_age) in sorted(REGION_DEMOGRAPHICS.items()):
        if demographic_year == year:
            writer.writerow([demographic_year, region, population, f"{median_age:.1f}"])
    return buffer.getvalue()


def failure_orphan_station_csv() -> str:
    rows = [
        ("PX", "2026-06-01T00:00:00Z", "11.0", "1.0", "false"),
        ("PX", "2026-06-01T12:00:00Z", "14.0", "2.0", "false"),
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(OBSERVATION_COLUMNS)
    writer.writerows(rows)
    return buffer.getvalue()


def failure_negative_precip_csv() -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(OBSERVATION_COLUMNS)
    writer.writerow(("P1", "2026-06-02T00:00:00Z", "12.0", "-1.5", "false"))
    return buffer.getvalue()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def generate(data: Path = DEFAULT_DATA_DIR) -> dict[str, int]:
    """Regenerate every fixture under ``data`` deterministically."""
    if data.exists():
        shutil.rmtree(data)

    api_dir = data / "api"
    api_dir.mkdir(parents=True)
    for registry_date, payload in build_registry_payloads().items():
        _write_json(api_dir / f"place-registry-{registry_date}.json", payload)

    civic_dir = data / "civic"
    civic_dir.mkdir()
    _write_json(civic_dir / "places.geojson", build_places_geojson())

    weather_dir = data / "weather"
    weather_dir.mkdir()
    archive_count = 0
    observation_count = 0
    for month in WEATHER_MONTHS:
        rows = observation_rows(month)
        observation_count += len(rows)
        members = {
            f"observations-{month}-{day}.csv": observations_csv(
                [r for r in rows if r["observed_at"].startswith(f"{month}-{day}")]
            )
            for day in OBSERVATION_DAYS
        }
        write_zip(weather_dir / f"weather-{month}.zip", members)
        archive_count += 1

    demographics_dir = data / "demographics"
    demographics_dir.mkdir()
    for year in DEMOGRAPHIC_YEARS:
        (demographics_dir / f"demographics-{year}.csv").write_text(
            demographics_csv(year), encoding="utf-8"
        )

    failures_dir = data / "failures"
    failures_dir.mkdir()
    (failures_dir / "observations_orphan_station.csv").write_text(
        failure_orphan_station_csv(), encoding="utf-8"
    )
    (failures_dir / "precip_negative.csv").write_text(
        failure_negative_precip_csv(), encoding="utf-8"
    )

    return {
        "places": len(BASELINE_PLACES),
        "registry_dates": len(build_registry_payloads()),
        "observations": observation_count,
        "weather_archives": archive_count,
        "demographics_rows": len(REGION_DEMOGRAPHICS),
        "failures": 2,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    args = parser.parse_args()
    summary = generate(args.data_dir)
    print(json.dumps(summary, indent=2, sort_keys=True))
