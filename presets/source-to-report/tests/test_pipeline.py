"""The built lakehouse's tables, checks and report agree with the ground truth."""

from __future__ import annotations

import csv
import json

import pytest


def _rows(path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_counts_and_checks(lakehouse) -> None:
    summary = lakehouse.summary

    assert summary["counts"] == {
        "readings": 52_560,
        "months": 12,
        "stations": 3,
        "pollutants": 2,
        "claims": 72,
        "claims_differ": 3,
        "claims_missing": 0,
        "pm25_station_days_over": 40,
        "no2_station_days_over": 0,
        "tables": 5,
    }
    assert all(check["ok"] for check in summary["checks"])
    assert summary["headlines"]["pm25_highest_station"] == "Northgate"
    assert summary["headlines"]["coverage_min"] == 1.0


def test_published_figures_match_the_injected_errors(lakehouse) -> None:
    manifest = json.loads((lakehouse.data / "raw" / "manifest.json").read_text(encoding="utf-8"))
    expected = {
        (error["month"], error["station_id"], error["pollutant"])
        for error in manifest["injected_errors"]
    }

    claims = _rows(lakehouse.out / "data" / "claims_checked.csv")
    differing = {
        (row["month"][:7], row["station_id"], row["pollutant"])
        for row in claims
        if row["verdict"] == "differs"
    }
    assert len(claims) == 72
    assert differing == expected
    assert not [row for row in claims if row["verdict"] == "missing"]


def test_annual_means_match_the_generator(lakehouse) -> None:
    manifest = json.loads((lakehouse.data / "raw" / "manifest.json").read_text(encoding="utf-8"))
    annual = {
        (row["station_id"], row["pollutant"]): float(row["mean_value"])
        for row in _rows(lakehouse.out / "data" / "annual_station.csv")
    }

    for station_id, pollutants in manifest["annual_means"].items():
        for pollutant, expected in pollutants.items():
            assert annual[(station_id, pollutant)] == pytest.approx(expected, abs=1e-6)


def test_report_is_self_contained(lakehouse) -> None:
    report = (lakehouse.out / "index.html").read_text(encoding="utf-8")

    assert report.startswith("<!doctype html>")
    assert "<title>Air quality brief" in report
    assert "<script" not in report
    assert "<link" not in report
    assert 'src="http' not in report
    assert 'href="http' not in report
    assert "notes/2025-03.md:5" in report
    assert "10.4" in report


def test_provenance_pins_every_input(lakehouse) -> None:
    provenance = json.loads((lakehouse.out / "provenance.json").read_text(encoding="utf-8"))
    summary = json.loads((lakehouse.out / "summary.json").read_text(encoding="utf-8"))

    assert len(provenance["inputs"]) == 13  # the pull and twelve notes
    assert provenance["tables"] == summary["tables"]
    for item in provenance["inputs"]:
        assert len(item["sha256"]) == 64
        assert item["bytes"] > 0
    assert provenance["git"]["commit"]


def test_every_table_the_report_links_to_exists(lakehouse) -> None:
    for name in (
        "annual_station",
        "claims_checked",
        "days_over",
        "monthly_station",
        "station_coverage",
    ):
        assert (lakehouse.out / "data" / f"{name}.csv").is_file(), name
    for name in ("pm25_monthly", "pm25_annual", "no2_annual", "pm25_days"):
        assert (lakehouse.out / "charts" / f"{name}.svg").is_file(), name
