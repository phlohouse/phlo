"""Effective-dated reference data helpers.

Validity windows are half-open: a row covers [valid_from, valid_to),
and open ends (None) mean unbounded on that side. Dates are coerced
freely among date, datetime, and ISO strings. effective_join attaches
the reference row valid at each fact's event time; unmatched facts are
surfaced by assert_no_reference_gap instead of silently dropped.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any


def _coerce_date(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def row_effective_at(
    row: Mapping[str, Any],
    as_of: date | datetime | str,
    *,
    start_field: str = "valid_from",
    end_field: str = "valid_to",
) -> bool:
    """Return whether a row is effective at a point in time."""
    target = _coerce_date(as_of)
    if target is None:
        return False
    start = _coerce_date(row.get(start_field))
    end = _coerce_date(row.get(end_field))
    starts_before_target = start is None or start <= target
    ends_after_target = end is None or target < end
    return starts_before_target and ends_after_target


def reference_snapshot(
    rows: Iterable[Mapping[str, Any]],
    *,
    as_of: date | datetime | str,
    key_field: str,
    start_field: str = "valid_from",
    end_field: str = "valid_to",
) -> dict[str, Mapping[str, Any]]:
    """Return effective reference rows keyed by key field."""
    snapshot: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if row_effective_at(row, as_of, start_field=start_field, end_field=end_field):
            snapshot[str(row[key_field])] = row
    return snapshot


def effective_join(
    facts: Iterable[Mapping[str, Any]],
    references: Iterable[Mapping[str, Any]],
    *,
    fact_key: str,
    reference_key: str,
    fact_time: str,
    prefix: str = "ref_",
) -> list[dict[str, Any]]:
    """Join facts to reference rows valid at each fact's event time."""
    refs = list(references)
    joined: list[dict[str, Any]] = []
    for fact in facts:
        match = next(
            (
                ref
                for ref in refs
                if str(ref[reference_key]) == str(fact[fact_key])
                and row_effective_at(ref, fact[fact_time])
            ),
            None,
        )
        row = dict(fact)
        if match is not None:
            row.update({f"{prefix}{key}": value for key, value in match.items()})
        joined.append(row)
    return joined


def assert_no_reference_gap(
    facts: Iterable[Mapping[str, Any]],
    references: Iterable[Mapping[str, Any]],
    *,
    fact_key: str,
    reference_key: str,
    fact_time: str,
) -> list[dict[str, Any]]:
    """Return facts that lack an effective reference row."""
    refs = list(references)
    missing: list[dict[str, Any]] = []
    for fact in facts:
        has_match = any(
            str(ref[reference_key]) == str(fact[fact_key])
            and row_effective_at(ref, fact[fact_time])
            for ref in refs
        )
        if not has_match:
            missing.append(dict(fact))
    return missing
