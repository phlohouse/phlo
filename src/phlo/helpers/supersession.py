"""Helpers for corrected, superseded, and latest-view records.

Rows carry a truthy "invalidated" marker that hides them from latest
views unless explicitly included. latest_records picks one row per
business key by order_field comparison (later wins); correction_chain
follows corrects_record_id links forward from an original ID.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def supersession_key(row: Mapping[str, Any], *fields: str, separator: str = "|") -> str:
    """Build a stable business key for supersession/correction groups."""
    return separator.join(str(row.get(field, "")) for field in fields)


def latest_records(
    rows: Iterable[Mapping[str, Any]],
    *,
    key_fields: list[str],
    order_field: str,
    include_invalidated: bool = False,
    invalidated_field: str = "invalidated",
) -> list[dict[str, Any]]:
    """Return the latest record for each business key."""
    latest: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not include_invalidated and bool(row.get(invalidated_field)):
            continue
        key = supersession_key(row, *key_fields)
        current = latest.get(key)
        if current is None or row[order_field] > current[order_field]:
            latest[key] = row
    return [dict(row) for row in latest.values()]


def correction_chain(
    rows: Iterable[Mapping[str, Any]],
    *,
    original_id: str,
    id_field: str = "record_id",
    corrects_field: str = "corrects_record_id",
) -> list[dict[str, Any]]:
    """Return a correction chain starting at an original record ID."""
    remaining = [dict(row) for row in rows]
    chain: list[dict[str, Any]] = []
    current_id: str | None = original_id
    while current_id is not None:
        current = next((row for row in remaining if str(row.get(id_field)) == current_id), None)
        if current is None:
            break
        chain.append(current)
        current_id = next(
            (
                str(row[id_field])
                for row in remaining
                if str(row.get(corrects_field) or "") == str(current[id_field])
            ),
            None,
        )
    return chain


def invalidated_record_filter(
    rows: Iterable[Mapping[str, Any]],
    *,
    invalidated_field: str = "invalidated",
) -> list[dict[str, Any]]:
    """Return rows that are not marked invalidated."""
    return [dict(row) for row in rows if not bool(row.get(invalidated_field))]
