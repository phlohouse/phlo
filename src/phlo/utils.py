"""Common utility functions for Phlo.

compact_dict() drops None-valued keys; dedupe_preserve_order() keeps first
occurrences in input order.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, TypeVar

_T = TypeVar("_T")


def compact_dict(d: Mapping[str, Any]) -> dict[str, Any]:
    """Remove None values from a dictionary."""
    return {k: v for k, v in d.items() if v is not None}


def dedupe_preserve_order(values: Iterable[_T]) -> list[_T]:
    """Return unique values in input order."""
    seen: set[_T] = set()
    deduped: list[_T] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped
