"""The source pull is deterministic and its manifest is trustworthy.

These tests check the generator independently of the lakehouse: they recompute
the monthly means from the raw readings and compare them with the manifest.
"""

from __future__ import annotations

import json

import pytest
from conftest import generate_fixtures

from source_to_report.domain import POLLUTANTS, STATIONS


def test_manifest_counts_match_the_readings(fixture_data) -> None:
    manifest = json.loads((fixture_data / "raw" / "manifest.json").read_text(encoding="utf-8"))
    readings = json.loads((fixture_data / "raw" / "readings.json").read_text(encoding="utf-8"))

    assert manifest["readings"] == len(readings) == 52_560
    assert manifest["claims"] == len(manifest["notes"]) * len(STATIONS) * len(POLLUTANTS) == 72
    assert manifest["period"] == {"start": "2025-01-01T00:00:00Z", "end": "2025-12-31T23:00:00Z"}
    assert set(manifest["annual_means"]) == {station.id for station in STATIONS}


def test_monthly_means_recomputed_from_readings(fixture_data) -> None:
    manifest = json.loads((fixture_data / "raw" / "manifest.json").read_text(encoding="utf-8"))
    readings = json.loads((fixture_data / "raw" / "readings.json").read_text(encoding="utf-8"))

    for month in ("2025-01", "2025-04", "2025-12"):
        for station in STATIONS:
            for pollutant in POLLUTANTS:
                values = [
                    row["value"]
                    for row in readings
                    if row["observed_date"].startswith(month)
                    and row["station_id"] == station.id
                    and row["pollutant"] == pollutant.id
                ]
                expected = sum(values) / len(values)
                assert manifest["monthly_means"][month][station.id][pollutant.id] == pytest.approx(
                    expected, abs=1e-6
                )


def test_generation_is_byte_identical(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate_fixtures(first)
    generate_fixtures(second)

    for relative in ("raw/readings.json", "raw/manifest.json", "notes/2025-03.md"):
        assert (first / relative).read_bytes() == (second / relative).read_bytes(), relative


def test_injected_errors_are_the_only_altered_figures(fixture_data) -> None:
    manifest = json.loads((fixture_data / "raw" / "manifest.json").read_text(encoding="utf-8"))
    errors = manifest["injected_errors"]

    assert len(errors) == 3
    for error in errors:
        measured = manifest["monthly_means"][error["month"]][error["station_id"]][
            error["pollutant"]
        ]
        assert measured == pytest.approx(error["measured"], abs=1e-6)
        assert abs(error["published"] - measured) > 0.05
