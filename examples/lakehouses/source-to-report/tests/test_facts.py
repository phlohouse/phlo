"""Report lookups are strict, and ambiguity is an error rather than a guess."""

from __future__ import annotations

import csv
import json

import pytest

from source_to_report.facts import FactError, Facts, load


def _write_table(path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture()
def facts(tmp_path) -> Facts:
    data = tmp_path / "data"
    data.mkdir()
    _write_table(
        data / "annual_station.csv",
        [
            {"station_id": "riverside", "pollutant": "PM2.5", "mean_value": 8.6},
            {"station_id": "harbour", "pollutant": "PM2.5", "mean_value": 7.0},
            {"station_id": "northgate", "pollutant": "PM2.5", "mean_value": 10.4},
        ],
    )
    summary = {
        "counts": {"readings": 52_560},
        "checks": [{"name": "coverage_at_least_95", "ok": True, "detail": "100%"}],
    }
    (tmp_path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return load(tmp_path)


def test_row_and_value_are_strict(facts: Facts) -> None:
    assert (
        facts.value("annual_station", "mean_value", station_id="harbour", pollutant="PM2.5") == 7.0
    )
    assert facts.summary("counts.readings") == 52_560
    assert facts.check_ok("coverage_at_least_95") is True


def test_extreme_returns_the_row(facts: Facts) -> None:
    worst = facts.extreme("annual_station", "mean_value", mode="max", pollutant="PM2.5")
    assert worst["station_id"] == "northgate"
    best = facts.extreme("annual_station", "mean_value", mode="min", pollutant="PM2.5")
    assert best["station_id"] == "harbour"


def test_missing_table_row_column_and_summary_all_raise(facts: Facts) -> None:
    with pytest.raises(FactError, match="missing table"):
        facts.rows("not_there")
    with pytest.raises(FactError, match="no row matches"):
        facts.row("annual_station", station_id="atlantis")
    with pytest.raises(FactError, match="no column"):
        facts.value("annual_station", "not_a_column", station_id="harbour")
    with pytest.raises(FactError, match="summary has no value"):
        facts.summary("counts.not_there")


def test_ambiguous_lookup_raises(tmp_path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    _write_table(
        data / "annual_station.csv",
        [
            {"station_id": "riverside", "pollutant": "PM2.5", "mean_value": 8.6},
            {"station_id": "riverside", "pollutant": "NO2", "mean_value": 30.3},
        ],
    )
    (tmp_path / "summary.json").write_text('{"checks": []}', encoding="utf-8")
    facts = load(tmp_path)

    with pytest.raises(FactError, match="rows match"):
        facts.row("annual_station", station_id="riverside")


def test_total_sums_only_matching_rows(facts: Facts) -> None:
    assert facts.total("annual_station", "mean_value") == pytest.approx(26.0)
    assert facts.total("annual_station", "mean_value", pollutant="PM2.5") == pytest.approx(26.0)
