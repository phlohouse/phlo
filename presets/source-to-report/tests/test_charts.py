"""Charts are deterministic, self-contained and honest about their scale."""

from __future__ import annotations

import re

import pytest

from source_to_report.charts import PALETTE, Series, bar_chart, line_chart


def test_line_chart_draws_one_line_and_point_per_value() -> None:
    svg = line_chart(
        [Series("Riverside", [8.0, 9.0, 7.0]), Series("Harbour", [6.0, 6.5, 5.5])],
        ["Jan", "Feb", "Mar"],
        y_label="µg/m³",
        title="Monthly mean",
    )

    assert svg.count("<polyline") == 2
    assert svg.count("<circle") == 6
    assert 'stroke="#2563eb"' in svg
    assert 'stroke="#3f3f46"' in svg
    assert "Monthly mean" in svg


def test_chart_labels_are_escaped() -> None:
    svg = bar_chart(["<script>"], [1.0], y_label="count", title="<unsafe>")

    assert "&lt;script&gt;" in svg
    assert "<script>" not in svg


def test_integer_ticks_stay_integers() -> None:
    svg = bar_chart(
        ["Jan", "Feb", "Mar", "Apr"],
        [12.0, 6.0, 1.0, 0.0],
        y_label="station-days",
        title="days over",
        integer_ticks=True,
    )

    labels = re.findall(r'<text[^>]*font-size="11"[^>]*>([^<]+)</text>', svg)
    numeric = [label for label in labels if re.fullmatch(r"\d+(?:\.\d+)?", label)]
    assert numeric
    assert all("." not in label for label in numeric)


def test_threshold_is_drawn_when_given() -> None:
    svg = bar_chart(
        ["Riverside", "Harbour"],
        [8.6, 7.0],
        y_label="µg/m³",
        title="annual",
        threshold=10.0,
        threshold_label="objective 10 µg/m³",
    )

    assert "stroke-dasharray" in svg
    assert "objective 10 µg/m³" in svg


def test_mismatched_inputs_are_rejected() -> None:
    with pytest.raises(ValueError):
        line_chart([Series("a", [1.0])], ["Jan", "Feb"], y_label="x", title="t")
    with pytest.raises(ValueError):
        bar_chart(["a"], [1.0, 2.0], y_label="x", title="t")
    with pytest.raises(ValueError):
        bar_chart([], [], y_label="x", title="t")


def test_palette_is_stable() -> None:
    assert PALETTE[0] == "#2563eb"
