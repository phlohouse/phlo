"""Dependency-free SVG charts.

The report must open offline, from one file, on any machine. These functions
draw deterministic SVG: no JavaScript, no fonts to fetch, no random ids. The
same data always produces the same bytes, which is what makes the report's
reproducibility check meaningful.
"""

from __future__ import annotations

import html
import math
from collections.abc import Sequence
from dataclasses import dataclass

PALETTE = ("#2563eb", "#3f3f46", "#d95f4b", "#7c3aed", "#0891b2")
INK = "#0b0b0b"
MUTED = "#8a8783"
GRID = "#ecebe9"
THRESHOLD = "#d95f4b"
FONT = "Inter, 'InterVariable', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"


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


def _svg(width: int, height: int, title: str, body: list[str]) -> str:
    label = html.escape(title)
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="100%" height="auto" role="img" style="font-family:{FONT}">',
            f"<title>{label}</title>",
            *body,
            "</svg>",
        ]
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
    height: int = 340,
) -> str:
    """Draw a line chart, one line per series, with an optional threshold."""
    if not series:
        raise ValueError("a line chart needs at least one series")
    count = len(x_labels)
    if count < 2:
        raise ValueError("a line chart needs at least two x labels")
    if any(len(item.values) != count for item in series):
        raise ValueError("every series must have one value per x label")

    left, right, top, bottom = 58, 18, 60, 44
    plot_width = width - left - right
    plot_height = height - top - bottom
    ceiling, step = _scale([value for item in series for value in item.values], threshold)
    baseline = top + plot_height

    body: list[str] = []
    for tick in range(int(round(ceiling / step)) + 1):
        value = step * tick
        y = baseline - (value / ceiling) * plot_height
        body.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_width}" y2="{y:.1f}" stroke="{GRID}"/>'
        )
        body.append(
            f'<text x="{left - 10}" y="{y + 4:.1f}" text-anchor="end" font-size="11" '
            f'fill="{MUTED}">{_number(value)}</text>'
        )
    body.append(
        f'<text x="{left}" y="{top - 28}" font-size="11" fill="{MUTED}">{html.escape(y_label)}</text>'
    )

    stride = max(1, math.ceil(count / 12))
    for index, label in enumerate(x_labels):
        if index % stride and index != count - 1:
            continue
        x = left + (index / (count - 1)) * plot_width
        body.append(
            f'<text x="{x:.1f}" y="{baseline + 20}" text-anchor="middle" font-size="11" '
            f'fill="{MUTED}">{html.escape(str(label))}</text>'
        )

    for index, item in enumerate(series):
        colour = PALETTE[index % len(PALETTE)]
        points = " ".join(
            f"{left + (position / (count - 1)) * plot_width:.1f},"
            f"{baseline - (float(value) / ceiling) * plot_height:.1f}"
            for position, value in enumerate(item.values)
        )
        body.append(
            f'<polyline points="{points}" fill="none" stroke="{colour}" stroke-width="2.2" '
            'stroke-linejoin="round" stroke-linecap="round"/>'
        )
        for position, value in enumerate(item.values):
            x = left + (position / (count - 1)) * plot_width
            y = baseline - (float(value) / ceiling) * plot_height
            body.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.8" fill="{colour}">'
                f"<title>{html.escape(item.name)} · {html.escape(str(x_labels[position]))}: "
                f"{_number(float(value))}</title></circle>"
            )

        legend_x = left + 118 * index
        body.append(
            f'<line x1="{legend_x}" y1="{top - 44}" x2="{legend_x + 16}" y2="{top - 44}" '
            f'stroke="{colour}" stroke-width="3"/>'
        )
        body.append(
            f'<text x="{legend_x + 22}" y="{top - 40}" font-size="12" fill="{INK}">'
            f"{html.escape(item.name)}</text>"
        )

    if threshold is not None:
        y = baseline - (threshold / ceiling) * plot_height
        body.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_width}" y2="{y:.1f}" '
            f'stroke="{THRESHOLD}" stroke-width="1.4" stroke-dasharray="5 4"/>'
        )
        if threshold_label:
            body.append(
                f'<text x="{left + plot_width}" y="{top - 18}" text-anchor="end" font-size="11" '
                f'fill="{THRESHOLD}">{html.escape(threshold_label)}</text>'
            )

    return _svg(width, height, title, body)


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
    height: int = 320,
) -> str:
    """Draw a bar chart with optional threshold line and value labels."""
    if not categories:
        raise ValueError("a bar chart needs at least one category")
    if len(categories) != len(values):
        raise ValueError("every category needs exactly one value")

    left, right, top, bottom = 58, 18, 48, 44
    plot_width = width - left - right
    plot_height = height - top - bottom
    ceiling, step = _scale([float(value) for value in values], threshold, integer=integer_ticks)
    baseline = top + plot_height
    slot = plot_width / len(categories)
    bar_width = slot * 0.56

    body: list[str] = []
    for tick in range(int(round(ceiling / step)) + 1):
        value = step * tick
        y = baseline - (value / ceiling) * plot_height
        body.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_width}" y2="{y:.1f}" stroke="{GRID}"/>'
        )
        body.append(
            f'<text x="{left - 10}" y="{y + 4:.1f}" text-anchor="end" font-size="11" '
            f'fill="{MUTED}">{_number(value)}</text>'
        )
    body.append(
        f'<text x="{left}" y="{top - 18}" font-size="11" fill="{MUTED}">{html.escape(y_label)}</text>'
    )

    for index, (category, value) in enumerate(zip(categories, values, strict=True)):
        height_px = (float(value) / ceiling) * plot_height
        x = left + index * slot + (slot - bar_width) / 2
        y = baseline - height_px
        body.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{max(height_px, 1.0):.1f}" '
            f'rx="2" fill="{colour}"><title>{html.escape(str(category))}: '
            f"{_number(float(value))}{html.escape(value_suffix)}</title></rect>"
        )
        body.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{y - 6:.1f}" text-anchor="middle" '
            f'font-size="11" fill="{INK}">{_number(float(value))}{html.escape(value_suffix)}</text>'
        )
        body.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{baseline + 20}" text-anchor="middle" '
            f'font-size="11" fill="{MUTED}">{html.escape(str(category))}</text>'
        )

    if threshold is not None:
        y = baseline - (threshold / ceiling) * plot_height
        body.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_width}" y2="{y:.1f}" '
            f'stroke="{THRESHOLD}" stroke-width="1.4" stroke-dasharray="5 4"/>'
        )
        if threshold_label:
            body.append(
                f'<text x="{left + plot_width}" y="{top - 18}" text-anchor="end" font-size="11" '
                f'fill="{THRESHOLD}">{html.escape(threshold_label)}</text>'
            )

    return _svg(width, height, title, body)
