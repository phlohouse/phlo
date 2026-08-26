"""Canonical shipment-state resolution and SLA arithmetic.

Pure functions only: pytest exercises this module against generated fixtures
and failure fixtures without containers, and the dbt model
``canonical_shipment_state`` implements the same ordering contract in SQL
(latest ``event_time`` wins; ties break by exception severity; a delivery that
arrives after an exception clears it).

Ordering contract
    1. The canonical state of a shipment is the state of its event with the
       greatest ``event_time``.
    2. If two events share the greatest ``event_time``, the more severe state
       wins (exception > delivered > pickup/in_transit).
    3. If two events share the greatest ``event_time`` but disagree on the
       state, the winner would depend on row order instead of data; such a
       tie is ambiguous and must fail loudly rather than guess.
"""

from __future__ import annotations

import pandas as pd

SEVERITY = {"pickup": 1, "in_transit": 1, "delivered": 2, "exception": 3}


def assert_unambiguous_event_order(events: pd.DataFrame) -> None:
    """Fail when one shipment's latest timestamp carries contradictory states.

    Two events at the same maximal ``event_time`` with different event types
    make the canonical state depend on row order instead of data, so any such
    tie raises regardless of severity: ordering is decided by timestamps
    alone, never by ingestion order.
    """
    for shipment_id, group in events.groupby("shipment_id"):
        latest = group[group["event_time"] == group["event_time"].max()]
        if latest["event_type"].nunique() > 1:
            raise ValueError(
                f"ambiguous canonical state for {shipment_id}: "
                f"{sorted(latest['event_type'])} share event_time {latest['event_time'].iloc[0]}"
            )


def resolve_canonical_states(events: pd.DataFrame) -> pd.DataFrame:
    """One canonical state per shipment with contradiction counts flagged.

    A shipment is contradictory when its feed contains both a ``delivered`` and
    an ``exception`` event. The later timestamp decides which state wins; the
    contradiction itself stays visible as ``contradiction_count`` so downstream
    consumers can audit carrier data quality instead of silently trusting the
    winner.
    """
    assert_unambiguous_event_order(events)
    ordered = events.assign(_severity=events["event_type"].map(SEVERITY)).sort_values(
        ["shipment_id", "event_time", "_severity"], ascending=[True, True, True], kind="stable"
    )
    rows: list[dict[str, object]] = []
    for shipment_id, group in ordered.groupby("shipment_id"):
        # Contract step 1: keep only events at the greatest event_time.
        latest = group[group["event_time"] == group["event_time"].max()]
        # Contract step 2: within that set the most severe state wins
        # (step 3 ambiguity is already rejected by the assertion above).
        winner = latest.sort_values("_severity", kind="stable").iloc[-1]
        saw_delivered = bool((group["event_type"] == "delivered").any())
        saw_exception = bool((group["event_type"] == "exception").any())
        rows.append(
            {
                "shipment_id": shipment_id,
                "carrier": winner["carrier"],
                "canonical_state": winner["event_type"],
                "state_as_of": winner["event_time"],
                "location": winner["location"],
                "contradiction_count": int(saw_delivered and saw_exception),
            }
        )
    return pd.DataFrame(rows)


def compute_transit_hours(events: pd.DataFrame) -> pd.DataFrame:
    """Pickup-to-delivery transit hours per shipment canonically delivered.

    Mirrors the dbt model: only shipments whose canonical state is
    ``delivered`` contribute, so a contradictory exception keeps a shipment
    out of the SLA mart while the contradiction stays flagged upstream.
    """
    resolved = resolve_canonical_states(events)
    delivered_ids = set(resolved.loc[resolved["canonical_state"] == "delivered", "shipment_id"])
    rows: list[dict[str, object]] = []
    for shipment_id, group in events.groupby("shipment_id"):
        if shipment_id not in delivered_ids:
            continue
        pickups = group.loc[group["event_type"] == "pickup", "event_time"]
        deliveries = group.loc[group["event_type"] == "delivered", "event_time"]
        if pickups.empty or deliveries.empty:
            continue
        rows.append(
            {
                "shipment_id": shipment_id,
                "carrier": group["carrier"].iloc[0],
                "pickup_at": pickups.min(),
                "delivered_at": deliveries.max(),
                "transit_hours": (
                    pd.to_datetime(deliveries.max()) - pd.to_datetime(pickups.min())
                ).total_seconds()
                / 3600.0,
            }
        )
    return pd.DataFrame(rows)


def evaluate_sla(transit_hours: float, sla_hours: float) -> dict[str, object]:
    """Compare actual transit against the contractual allowance."""
    return {
        "sla_breached": bool(transit_hours > sla_hours),
        "breach_hours": round(max(transit_hours - sla_hours, 0.0), 2),
    }


def assert_sla_clock_positive(terms: pd.DataFrame) -> str | None:
    """Gate: every contractual SLA clock must be strictly positive."""
    negative = terms[pd.to_numeric(terms["sla_hours"]) <= 0]
    if not negative.empty:
        offenders = [
            f"{row.carrier_code}:{row.service_level}={row.sla_hours}"
            for row in negative.itertuples(index=False)
        ]
        return f"SLA clock must be positive, found {offenders}"
    return None
