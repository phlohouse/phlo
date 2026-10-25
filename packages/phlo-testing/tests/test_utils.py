"""Tests for phlo_testing data normalisation helpers."""

from __future__ import annotations

import pandas as pd

from phlo_testing.utils import to_dataframe, to_records


def test_to_dataframe_returns_existing_frame_unchanged() -> None:
    frame = pd.DataFrame({"id": [1]})

    assert to_dataframe(frame) is frame


def test_to_dataframe_builds_frame_from_records() -> None:
    frame = to_dataframe([{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])

    assert frame["id"].tolist() == [1, 2]
    assert frame["name"].tolist() == ["a", "b"]


def test_to_records_accepts_frames_and_record_lists() -> None:
    records = [{"id": 1, "name": "a"}]

    assert to_records(pd.DataFrame(records)) == records
    assert to_records(records) == records
