"""Business rules that complement the staged Pandera contracts."""

from __future__ import annotations

import pandas as pd

ACCEPTED_EVENT_TYPES = {"signup", "project_created", "feature_used", "release_viewed"}


def validate_events(events: pd.DataFrame) -> None:
    if events.event_id.duplicated().any():
        raise ValueError("Duplicate SaaS event ID")
    event_types = set(events.event_type)
    if not event_types.issubset(ACCEPTED_EVENT_TYPES):
        raise ValueError(f"Unsupported event type: {sorted(event_types - ACCEPTED_EVENT_TYPES)}")
    ordered = events.sort_values(["actor_id", "occurred_at"])
    if (
        not ordered.groupby("actor_id")["occurred_at"]
        .apply(lambda values: values.is_monotonic_increasing)
        .all()
    ):
        raise ValueError("Session event order is not monotonic")


def validate_freshness(events: pd.DataFrame, newest_expected: str) -> None:
    if events.occurred_at.max() < newest_expected:
        raise ValueError("Replay is stale: expected a newer event")
