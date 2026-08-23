"""Compatibility helpers for loading package entry points.

Shields callers from entry_points() signature drift across supported Python
versions by normalizing group filtering behind one function.

Shared entry-point scanning helper used across plugin discovery and the CLI.
"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Iterable


def entry_points_for_group(group: str) -> Iterable[importlib.metadata.EntryPoint]:
    """Return entry points for a group across supported Python versions."""
    try:
        return importlib.metadata.entry_points(group=group)
    except TypeError:
        all_entry_points = importlib.metadata.entry_points()

    select = getattr(all_entry_points, "select", None)
    if callable(select):
        return select(group=group)

    get = getattr(all_entry_points, "get", None)
    if callable(get):
        return get(group, [])

    return []
