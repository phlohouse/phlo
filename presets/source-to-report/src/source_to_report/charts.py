"""Charts, authored in Altair and rendered to static SVG.

Each figure is a Vega-Lite specification: the data, encodings and scales are
declared, and ``vl-convert`` draws the SVG at build time. The report stays one
self-contained file, with no JavaScript and no external assets, and the same
data still produces the same bytes.
"""

from __future__ import annotations

import html
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

import altair as alt
import pandas as pd
import vl_convert as vlc

PALETTE = ("#2563eb", "#3f3f46", "#d95f4b", "#7c3aed", "#0891b2")
INK = "#0b0b0b"
MUTED = "#8a8783"
GRID = "#ecebe9"
THRESHOLD = "#d95f4b"
FONT = "Inter, 'InterVariable', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"

# Whole ticks where the data is whole, one decimal elsewhere. Vega expression.
TICK_LABEL = (
    "datum.value === floor(datum.value) ? format(datum.value, 'd') : format(datum.value, '.1f')"
)


@dataclass(frozen=True)
class Series:
    """One line in a chart."""

    name: str
    values: Sequence[float]


def _number(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{round(value):,}"
    return f"{value:,.1f}"


def _scale(
    values: Sequence[float], threshold: float | None, *, integer: bool = False
) -> tuple[float, float]:
    """Return a round ceiling and tick step that fit the data.

    Steps are the familiar 1, 2, 5, 10 family, so axes read as 0/5/10/15
    rather than 0/3.75/7.5. A threshold on the ceiling gets one more step, so
    the dashed line never sits on the top edge.
    """
    top = max(
        [float(value) for value in values] + ([threshold] if threshold is not None else []) + [1.0]
    )
    magnitude = 10.0 ** math.floor(math.log10(top))
    minimum = 1.0 if integer else 0.0
    for factor in (0.2, 0.5, 1.0, 2.0, 5.0, 10.0):
        step = factor * magnitude
        if step < minimum:
            continue
        ceiling = math.ceil(top / step) * step
        if threshold is not None and ceiling <= threshold:
            ceiling += step
        if ceiling / step <= 6:
            return round(ceiling, 10), round(step, 10)
    step = 10.0 * magnitude
    ceiling = math.ceil(top / step) * step
    return round(ceiling, 10), round(step, 10)


def _styled(chart: alt.TopLevelMixin) -> alt.TopLevelMixin:
    """The house style every figure shares."""
    return chart.configure(
        font=FONT,
        background="transparent",
        view=alt.ViewConfig(stroke=None),
        axis=alt.AxisConfig(
            labelColor=MUTED,
            labelFontSize=11,
            titleColor=MUTED,
            titleFontSize=11,
            titleFontWeight="normal",
            domain=False,
            ticks=False,
            gridColor=GRID,
            labelPadding=7,
        ),
        legend=alt.LegendConfig(
            orient="bottom",
            direction="horizontal",
            title=None,
            labelColor=INK,
            labelFontSize=12,
            symbolType="stroke",
            symbolStrokeWidth=3,
            offset=8,
        ),
        range=alt.RangeConfig(category=list(PALETTE)),
    )


def _y_axis(label: str, ceiling: float, step: float) -> alt.Y:
    ticks = [round(step * index, 10) for index in range(int(round(ceiling / step)) + 1)]
    return alt.Y(
        "value:Q",
        scale=alt.Scale(domain=[0, ceiling], nice=False),
        axis=alt.Axis(
            values=ticks,
            title=label,
            titleAngle=0,
            titleAnchor="start",
            titleX=0,
            titleY=-12,
            labelExpr=TICK_LABEL,
        ),
    )


def _threshold(value: float, label: str, width: int) -> alt.TopLevelMixin:
    """A dashed rule, with its label in the top-right corner of the plot."""
    rule = (
        alt.Chart(pd.DataFrame({"threshold": [value]}))
        .mark_rule(color=THRESHOLD, strokeWidth=1.4, strokeDash=[5, 4])
        .encode(y="threshold:Q")
    )
    if not label:
        return rule
    text = (
        alt.Chart(pd.DataFrame({"label": [label]}))
        .mark_text(align="right", baseline="bottom", color=THRESHOLD, fontSize=11)
        .encode(text="label:N", x=alt.value(width), y=alt.value(-10))
    )
    return rule + text


def _stable_ids(svg: str, prefix: str) -> str:
    """Name clip paths after the chart, not vl-convert's process counter.

    vl-convert numbers ids from a global counter, so a second build in the same
    process gets different ones. The prefix also keeps ids unique when several
    charts are inlined into one page.
    """
    names: dict[str, str] = {}

    def rename(match: re.Match[str]) -> str:
        original = match.group(1)
        return names.setdefault(original, f"{prefix}-{len(names)}")

    svg = re.sub(r'id="([^"]+)"', lambda match: f'id="{rename(match)}"', svg)
    return re.sub(
        r"url\(#([^)]+)\)",
        lambda match: f"url(#{names.get(match.group(1), match.group(1))})",
        svg,
    )


def _svg(chart: alt.TopLevelMixin, title: str) -> str:
    """Render the specification and name the SVG for assistive technology."""
    svg = vlc.vegalite_to_svg(chart.to_dict())
    label = html.escape(title)
    prefix = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "chart"
    svg = _stable_ids(svg, prefix)
    svg = re.sub(r'(<svg[^>]*?) width="\d+" height="\d+"', r'\1 width="100%"', svg, count=1)
    svg = svg.replace("<svg ", f'<svg role="img" aria-label="{label}" ', 1)
    return re.sub(
        r"(<svg[^>]*>)",
        lambda match: f"{match.group(1)}<title>{label}</title>",
        svg,
        count=1,
    )


def line_chart(
    series: Sequence[Series],
    x_labels: Sequence[str],
    *,
    y_label: str,
    threshold: float | None = None,
    threshold_label: str = "",
    title: str,
    width: int = 760,
    height: int = 300,
) -> str:
    """Draw a line chart, one line per series, with an optional threshold."""
    if not series:
        raise ValueError("a line chart needs at least one series")
    count = len(x_labels)
    if count < 2:
        raise ValueError("a line chart needs at least two x labels")
    if any(len(item.values) != count for item in series):
        raise ValueError("every series must have one value per x label")

    ceiling, step = _scale([value for item in series for value in item.values], threshold)
    names = [item.name for item in series]
    frame = pd.DataFrame(
        [
            {"x": str(label), "series": item.name, "value": float(value)}
            for item in series
            for label, value in zip(x_labels, item.values, strict=True)
        ]
    )
    colour = alt.Color(
        "series:N",
        scale=alt.Scale(
            domain=names,
            range=[PALETTE[index % len(PALETTE)] for index in range(len(names))],
        ),
        legend=alt.Legend(title=None),
    )
    x = alt.X(
        "x:N",
        sort=[str(label) for label in x_labels],
        title=None,
        axis=alt.Axis(labelAngle=0, grid=False),
    )
    y = _y_axis(y_label, ceiling, step)
    base = alt.Chart(frame)
    layers: alt.TopLevelMixin = base.mark_line(strokeWidth=2.2, interpolate="linear").encode(
        x=x, y=y, color=colour
    ) + base.mark_point(filled=True, size=18, opacity=1).encode(x=x, y=y, color=colour)
    if threshold is not None:
        layers = layers + _threshold(threshold, threshold_label, width)
    return _svg(_styled(layers.properties(width=width, height=height)), title)


def bar_chart(
    categories: Sequence[str],
    values: Sequence[float],
    *,
    y_label: str,
    title: str,
    threshold: float | None = None,
    threshold_label: str = "",
    value_suffix: str = "",
    colour: str = PALETTE[0],
    integer_ticks: bool = False,
    width: int = 760,
    height: int = 300,
) -> str:
    """Draw a bar chart with optional threshold line and value labels."""
    if not categories:
        raise ValueError("a bar chart needs at least one category")
    if len(categories) != len(values):
        raise ValueError("every category needs exactly one value")

    ceiling, step = _scale([float(value) for value in values], threshold, integer=integer_ticks)
    frame = pd.DataFrame(
        [
            {
                "category": str(category),
                "value": float(value),
                "label": f"{_number(float(value))}{value_suffix}",
            }
            for category, value in zip(categories, values, strict=True)
        ]
    )
    x = alt.X(
        "category:N",
        sort=[str(category) for category in categories],
        title=None,
        axis=alt.Axis(labelAngle=0, grid=False),
    )
    y = _y_axis(y_label, ceiling, step)
    bars = (
        alt.Chart(frame)
        .mark_bar(color=colour, cornerRadiusEnd=2, width=alt.RelativeBandSize(0.56))
        .encode(x=x, y=y)
    )
    labels = (
        alt.Chart(frame).mark_text(dy=-7, color=INK, fontSize=11).encode(x=x, y=y, text="label:N")
    )
    layers: alt.TopLevelMixin = bars + labels
    if threshold is not None:
        layers = layers + _threshold(threshold, threshold_label, width)
    return _svg(_styled(layers.properties(width=width, height=height)), title)
