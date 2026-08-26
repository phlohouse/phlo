"""Commerce-domain quality checks over plain DataFrames.

Validators follow the ``quality_checks`` protocol: return ``None`` when the
batch passes and a violation string when it fails. pytest screens fixtures
with them and operators can run them diagnostically against live tables.
"""

from __future__ import annotations

import pandas as pd


def canonical_email(email: str) -> str:
    """Collapse an observed address to its canonical identity.

    Lowercase everything and strip plus-suffixes from the local part, so
    ``Alice.Anderson+legacy@example.com`` and ``alice.anderson@example.com``
    converge.
    """
    local, _, domain = str(email).strip().lower().partition("@")
    return f"{local.split('+', 1)[0]}@{domain}"


def canonicalize_series(emails: pd.Series) -> pd.Series:
    """Vectorized :func:`canonical_email` over a column of addresses."""
    stripped = emails.astype(str).str.strip().str.lower()
    local = stripped.str.split("@", regex=False).str[0].str.split("+", regex=False).str[0]
    domain = stripped.str.split("@", regex=False).str[1]
    return local + "@" + domain


def assert_orders_reference_known_customers(
    orders: pd.DataFrame, customers: pd.DataFrame
) -> str | None:
    """Every order must map onto a known commerce customer identity."""
    known = set(canonicalize_series(customers.email))
    unknown_orders = orders[~canonicalize_series(orders.email).isin(known)]
    if not unknown_orders.empty:
        offenders = [f"{row.order_id}@{row.email}" for row in unknown_orders.itertuples()][:5]
        return f"orders reference unknown customers: {offenders}"
    return None


def assert_order_totals_reconcile(
    base_orders: pd.DataFrame, replicated_orders: pd.DataFrame
) -> str | None:
    """The replicated order book must reconcile to source by count and value.

    Sling upserts by ``order_id``, so one current row per order is expected;
    drift in either row count or summed revenue means the incremental stream
    lost or duplicated an order.
    """
    if len(base_orders) != len(replicated_orders):
        return (
            f"order count mismatch: source {len(base_orders)} vs replicated "
            f"{len(replicated_orders)}"
        )
    source_total = round(float(base_orders.total_amount.sum()), 2)
    replica_total = round(float(replicated_orders.total_amount.sum()), 2)
    if abs(source_total - replica_total) > 0.005:
        return f"order revenue mismatch: source {source_total} vs replicated {replica_total}"
    return None
