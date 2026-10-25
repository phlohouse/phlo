"""Tests for fixture loading and saving in phlo_testing.placeholders."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from phlo_testing.placeholders import load_fixture, save_fixture


def test_fixture_round_trip_json_and_csv(tmp_path) -> None:
    records = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]

    json_path = tmp_path / "nested" / "fixture.json"
    save_fixture(records, json_path)
    assert load_fixture(json_path) == records

    csv_path = tmp_path / "fixture.csv"
    save_fixture(pd.DataFrame(records), csv_path)
    assert load_fixture(csv_path).to_dict("records") == records


def test_json_fixture_pretty_flag_controls_indentation(tmp_path) -> None:
    pretty = tmp_path / "pretty.json"
    compact = tmp_path / "compact.json"

    save_fixture({"id": 1}, pretty)
    save_fixture({"id": 1}, compact, pretty=False)

    assert json.loads(pretty.read_text()) == {"id": 1}
    assert "\n  " in pretty.read_text()
    assert compact.read_text() == '{"id": 1}'


def test_load_fixture_rejects_missing_and_unsupported_files(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="Fixture file not found"):
        load_fixture(tmp_path / "absent.json")

    unsupported = tmp_path / "fixture.txt"
    unsupported.write_text("data")
    with pytest.raises(ValueError, match="Unsupported fixture format"):
        load_fixture(unsupported)
