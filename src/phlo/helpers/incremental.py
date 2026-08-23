"""Incremental load and watermark helpers.

Resolves a watermark from runtime, stored, or default values and renders it
as a SQL predicate using the shared literal encoder. Also provides lookback
windows, immutable state updates that advance committed watermarks, and
unique changed-key extraction since a watermark.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from phlo.helpers.sql import literal


@dataclass(frozen=True, slots=True)
class Watermark:
    """Resolved incremental-load watermark."""

    column: str
    value: Any
    lookback: timedelta = timedelta(0)

    @property
    def effective_value(self) -> Any:
        """Return the value after applying lookback when possible."""
        if isinstance(self.value, datetime):
            return self.value - self.lookback
        return self.value


def resolve_watermark(
    *,
    column: str,
    runtime_value: Any = None,
    stored_value: Any = None,
    default: Any = None,
    lookback: timedelta | None = None,
) -> Watermark:
    """Resolve a watermark from runtime, stored, or default values."""
    value = runtime_value if runtime_value is not None else stored_value
    if value is None:
        value = default
    return Watermark(column=column, value=value, lookback=lookback or timedelta(0))


def watermark_where_clause(watermark: Watermark, *, inclusive: bool = False) -> str:
    """Render a SQL predicate for an incremental watermark."""
    op = ">=" if inclusive else ">"
    return f"{watermark.column} {op} {literal(watermark.effective_value)}"


def lookback_window(
    value: datetime, *, minutes: int = 0, hours: int = 0, days: int = 0
) -> datetime:
    """Apply a lookback window to a timestamp."""
    return value - timedelta(minutes=minutes, hours=hours, days=days)


def mark_watermark_committed(
    state: dict[str, Any], asset_key: str, watermark: Any
) -> dict[str, Any]:
    """Return a copy of state with an asset watermark advanced."""
    updated = dict(state)
    updated[asset_key] = watermark
    return updated


def _changed_key(row: Mapping[str, Any], fields: list[str]) -> Any:
    if len(fields) == 1:
        return row.get(fields[0])
    return tuple(row.get(field) for field in fields)


def changed_keys_since(
    rows: Iterable[Mapping[str, Any]],
    watermark: Watermark | Any,
    *,
    key_fields: str | Iterable[str],
    updated_at_field: str | None = None,
    inclusive: bool = False,
) -> list[Any]:
    """Return unique entity keys changed after a watermark."""
    fields = [key_fields] if isinstance(key_fields, str) else list(key_fields)
    if not fields:
        raise ValueError("key_fields must include at least one field")

    if isinstance(watermark, Watermark):
        threshold = watermark.effective_value
        updated_field = updated_at_field or watermark.column
    else:
        threshold = watermark
        updated_field = updated_at_field or "updated_at"

    changed: set[Any] = set()
    for row in rows:
        value = row.get(updated_field)
        if value is None:
            continue
        is_changed = value >= threshold if inclusive else value > threshold
        if is_changed:
            changed.add(_changed_key(row, fields))
    return sorted(changed, key=lambda value: str(value))
