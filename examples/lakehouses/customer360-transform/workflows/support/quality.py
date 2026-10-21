"""Support-domain quality checks over plain DataFrames.

Validators follow the ``quality_checks`` protocol: return ``None`` when the
batch passes and a violation string when it fails.
"""

from __future__ import annotations

import pandas as pd


def assert_resolved_after_created(tickets: pd.DataFrame) -> str | None:
    """A ticket can only resolve after it was created (open tickets are null)."""
    created = pd.to_datetime(tickets.created_at, utc=True)
    resolved = pd.to_datetime(tickets.resolved_at, utc=True)
    backdated = tickets[resolved.notna() & (resolved < created)]
    if not backdated.empty:
        offenders = [
            f"{row.ticket_id} resolved {row.resolved_at} < created {row.created_at}"
            for row in backdated.itertuples()
        ][:5]
        return f"tickets resolved before creation: {offenders}"
    return None


def assert_ticket_ids_unique(tickets: pd.DataFrame) -> str | None:
    """One delivery must not carry the same ticket id twice."""
    duplicated = tickets.ticket_id[tickets.ticket_id.duplicated()]
    if not duplicated.empty:
        offenders = sorted(duplicated.unique().tolist())[:5]
        return f"ticket_id repeated within delivery: {offenders}"
    return None
