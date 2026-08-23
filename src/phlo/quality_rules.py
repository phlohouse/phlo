"""Provider-neutral quality rule factories.

Factories producing QualityRule records (not_null, unique, freshness, range,
accepted_values) without binding to any execution provider. Invalid ranges
and empty accepted-value lists raise ValueError at construction time, before
a rule can reach a pipeline.
"""

from __future__ import annotations

from typing import Any

from phlo.helpers.quality import QualityRule


def not_null(*columns: str) -> QualityRule:
    """Create a not-null quality rule for one or more columns."""
    return QualityRule(kind="not_null", columns=list(columns), parameters={})


def unique(*columns: str) -> QualityRule:
    """Create a uniqueness quality rule for one or more columns."""
    return QualityRule(kind="unique", columns=list(columns), parameters={})


def freshness(column: str, *, hours: float) -> QualityRule:
    """Create a freshness quality rule for a timestamp column."""
    return QualityRule(kind="freshness", columns=[column], parameters={"max_age_hours": hours})


def range_between(
    column: str,
    *,
    min_value: float | int | None = None,
    max_value: float | int | None = None,
) -> QualityRule:
    """Create a numeric range quality rule."""
    if min_value is None and max_value is None:
        raise ValueError("range_between requires min_value, max_value, or both")
    return QualityRule(
        kind="range",
        columns=[column],
        parameters={"min_value": min_value, "max_value": max_value},
    )


def accepted_values(column: str, values: list[Any]) -> QualityRule:
    """Create an accepted-values quality rule."""
    accepted = list(values)
    if not accepted:
        raise ValueError("accepted_values requires at least one value")
    return QualityRule(kind="accepted_values", columns=[column], parameters={"values": accepted})


__all__ = ["accepted_values", "freshness", "not_null", "range_between", "unique"]
