"""Customer-domain quality checks.

Operates on DataFrames read from the replicated ``commerce_customers`` table
(diagnostics) or produced by the fixture generator (tests). Snapshot mode
means the table accumulates one row per customer per snapshot run, so
"current state" is always the newest row per ``customer_id``.
"""

from __future__ import annotations

import pandas as pd

from workflows.schemas.commerce import CustomerSchema

SEGMENTS = {"consumer", "business", "enterprise"}
REGIONS = {"north", "south", "east", "west"}


def current_customers(customers: pd.DataFrame) -> pd.DataFrame:
    """Collapse a snapshot-accumulated table to one row per customer.

    ``_phlo_ingested_at`` exists on staged lakehouse tables; fixture frames
    omit it and fall back to the source ``updated_at`` alone.
    """
    sort_keys = ["updated_at"]
    if "_phlo_ingested_at" in customers.columns:
        sort_keys.append("_phlo_ingested_at")
    ascending = [False] * len(sort_keys)
    ranked = customers.sort_values(sort_keys, ascending=ascending)
    return ranked.drop_duplicates("customer_id", keep="first")


def validate_customers(customers: pd.DataFrame) -> pd.DataFrame:
    """Validate the customer contract and return the current-state frame."""
    CustomerSchema.validate(customers)
    current = current_customers(customers)
    if not set(current.segment).issubset(SEGMENTS):
        raise ValueError("Unknown customer segment")
    if not set(current.region).issubset(REGIONS):
        raise ValueError("Unknown customer region")
    return current


def assert_watermark_never_regresses(previous_max: str | None, candidates: pd.DataFrame) -> str:
    """Fail when the newest candidate row predates the stream's watermark.

    A replication batch whose freshest row is older than the last replicated
    ``updated_at`` means the source went backwards or the cursor was reset;
    either way the run must stop instead of silently overwriting newer state.
    Returns the advanced watermark to persist for the next run.
    """
    candidate_max = candidates.updated_at.max()
    if previous_max is not None and candidate_max < previous_max:
        raise ValueError(f"watermark regression: max updated_at {candidate_max} < {previous_max}")
    return candidate_max
