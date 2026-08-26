"""Contracts for replayed SaaS events and account-plan snapshots."""

from __future__ import annotations

import pandera.pandas as pa
from pandera.typing import Series

ACCEPTED_EVENT_TYPES = ["signup", "project_created", "feature_used", "release_viewed"]


class EventsSchema(pa.DataFrameModel):
    event_id: Series[str] = pa.Field(unique=True)
    occurred_at: Series[str]
    account_id: Series[str]
    account_name: Series[str]
    actor_id: Series[str]
    actor_email: Series[str]
    event_type: Series[str] = pa.Field(isin=ACCEPTED_EVENT_TYPES)
    feature: Series[str] | None = pa.Field(nullable=True)
    experiment_variant: Series[str] | None = pa.Field(nullable=True)
    session_id: Series[str]
    release: Series[str]

    class Config:
        strict = False
        coerce = True


class AccountPlansSchema(pa.DataFrameModel):
    account_id: Series[str] = pa.Field(unique=True)
    plan: Series[str] = pa.Field(isin=["free", "pro", "enterprise"])
    seats: Series[int] = pa.Field(gt=0)
    effective_at: Series[str]

    class Config:
        strict = False
        coerce = True
