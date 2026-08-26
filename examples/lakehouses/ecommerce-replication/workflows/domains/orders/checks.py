"""Order-domain quality checks: composite keys, referential integrity,
arithmetic, payment reconciliation, and incremental watermark behavior.

All functions operate on plain DataFrames so they run against fixture output in
pytest and against diagnostic reads of replicated tables in live runs.
"""

from __future__ import annotations

import pandas as pd

from workflows.schemas.commerce import OrderLineSchema, OrderSchema, PaymentSchema

PAYMENT_TOLERANCE = 0.01


def validate_orders(orders: pd.DataFrame) -> None:
    OrderSchema.validate(orders)


def assert_order_line_integrity(orders: pd.DataFrame, lines: pd.DataFrame) -> None:
    """Composite-key uniqueness, arithmetic, and order-line referential integrity."""
    OrderLineSchema.validate(lines)
    duplicated = lines.duplicated(["order_id", "line_id"])
    if duplicated.any():
        offenders = lines[duplicated][["order_id", "line_id"]].head(5).to_dict("records")
        raise ValueError(f"Duplicate composite key (order_id, line_id): {offenders}")
    expected = (lines.quantity * lines.unit_price).round(2)
    if not expected.equals(lines.line_amount.round(2)):
        raise ValueError("Order-line amount does not equal quantity * unit_price")
    unknown = set(lines.order_id).difference(set(orders.order_id))
    if unknown:
        raise ValueError(f"Order lines reference unknown orders: {sorted(unknown)[:5]}")


def assert_payment_reconciliation(orders: pd.DataFrame, payments: pd.DataFrame) -> None:
    """Payments per order must not exceed the order total; delivered orders
    must be fully paid within tolerance."""
    PaymentSchema.validate(payments)
    unknown = set(payments.order_id).difference(set(orders.order_id))
    if unknown:
        raise ValueError(f"Payments reference unknown orders: {sorted(unknown)[:5]}")
    paid_per_order = payments.groupby("order_id").amount.sum().round(2)
    totals = orders.set_index("order_id").total_amount.round(2)
    overpaid = paid_per_order - totals.reindex(paid_per_order.index)
    if (overpaid > PAYMENT_TOLERANCE).any():
        offenders = overpaid[overpaid > PAYMENT_TOLERANCE].head(5).to_dict()
        raise ValueError(f"Payments exceed order totals: {offenders}")
    delivered = orders[orders.status == "delivered"].order_id
    underpaid = totals.reindex(delivered).fillna(0) - paid_per_order.reindex(delivered).fillna(0)
    if (underpaid > PAYMENT_TOLERANCE).any():
        offenders = underpaid[underpaid > PAYMENT_TOLERANCE].head(5).to_dict()
        raise ValueError(f"Delivered orders are not fully paid: {offenders}")


def assert_watermark_advances(previous_max: str | None, batch: pd.DataFrame) -> str:
    """Incremental batches may only contain rows at or after the watermark.

    Returns the advanced watermark. Raises on regression so a misconfigured
    incremental run cannot silently rewrite already-published history.
    """
    if batch.empty:
        return previous_max or ""
    batch_max = batch.updated_at.max()
    if previous_max is not None and batch.updated_at.min() < previous_max:
        stale = batch[batch.updated_at < previous_max]
        raise ValueError(
            f"incremental batch contains rows older than watermark "
            f"{previous_max}: first offender {stale.iloc[0].to_dict()}"
        )
    return batch_max
