"""Extraction is strict, located, and complete."""

from __future__ import annotations

import json

import pytest

from source_to_report.domain.extraction import ExtractionError, extract_claims
from source_to_report.domain.sources import read_notes


def test_every_published_figure_is_extracted(fixture_data) -> None:
    notes = read_notes(fixture_data)
    claims = extract_claims(notes)

    assert len(notes) == 12
    assert len(claims) == 72
    assert len({claim["claim_id"] for claim in claims}) == 72
    for claim in claims:
        assert claim["source_path"].startswith("notes/2025-")
        assert claim["line"] >= 1


def test_claims_point_at_the_line_that_states_them(fixture_data) -> None:
    notes = {note["path"]: note for note in read_notes(fixture_data)}
    for claim in extract_claims(list(notes.values())):
        stated = str(notes[claim["source_path"]]["body"]).splitlines()[claim["line"] - 1]
        assert f"{claim['value']:.1f}" in stated
        assert claim["station_id"].capitalize() in stated
        assert claim["pollutant"] in stated


def test_injected_errors_carry_their_published_value(fixture_data) -> None:
    manifest = json.loads((fixture_data / "raw" / "manifest.json").read_text(encoding="utf-8"))
    claims = {
        (claim["month"], claim["station_id"], claim["pollutant"]): claim
        for claim in extract_claims(read_notes(fixture_data))
    }
    for error in manifest["injected_errors"]:
        claim = claims[(error["month"], error["station_id"], error["pollutant"])]
        assert claim["value"] == pytest.approx(error["published"])


def test_a_malformed_figure_stops_the_build(tmp_path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "2025-01.md").write_text(
        "# Monitoring note — January 2025\n\nPM2.5 monthly mean: Riverside about eight.\n",
        encoding="utf-8",
    )
    with pytest.raises(ExtractionError, match="cannot read figure"):
        extract_claims(read_notes(tmp_path))


def test_an_unknown_station_stops_the_build(tmp_path) -> None:
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "2025-01.md").write_text(
        "# Monitoring note — January 2025\n\n"
        "PM2.5 monthly mean: Atlantis 8.1 µg/m³, Harbour 7.4 µg/m³, Northgate 9.0 µg/m³.\n"
        "NO2 monthly mean: Riverside 29.0 µg/m³, Harbour 19.0 µg/m³, Northgate 36.0 µg/m³.\n",
        encoding="utf-8",
    )
    with pytest.raises(ExtractionError, match="unknown station"):
        extract_claims(read_notes(tmp_path))
