"""Orders-domain Python transforms: read-time version collapse and status gate.

This folder is one of the four repeated Python transform folders exercised by
this example. It registers a real Dagster asset (``order_current_state``) that
depends on the Sling replication output ``sling_shipments_orders``, collapses
the append-only replicated versions to one current row per order, and writes
the result back to Iceberg through the phlo_iceberg helpers.

Status regression is gated here rather than at ingestion: ``phlo.ingest.sling``
does not expose a ``quality_checks`` hook, so the first Python transform in the
orders domain blocks promotion when a later order version moves the status
backwards.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import dagster as dg
import pandas as pd
from pandera.typing import DataFrame as PaDataFrame

from workflows.schemas.logistics import OrderCurrentStateSchema, ShipmentOrderSchema

STATUS_RANK = {
    "pending": 0,
    "allocated": 1,
    "shipped": 2,
    "delivered": 3,
    "cancelled": 4,
}

SOURCE_TABLE = "raw.shipments_orders"
TARGET_TABLE = "raw.order_current_state"


def latest_order_versions(orders: pd.DataFrame) -> pd.DataFrame:
    """Collapse replicated order versions to one current row per order.

    The winner is the version with the greatest ``updated_at``; ties are broken
    deterministically by status rank so repeated runs are byte-stable.
    """
    if orders.empty:
        return orders.assign(current_status=pd.Series(dtype="object"))
    ranked = orders.sort_values(
        ["order_id", "updated_at", "status"],
        key=lambda column: column.map(STATUS_RANK) if column.name == "status" else column,
        kind="stable",
    )
    latest = ranked.drop_duplicates(subset="order_id", keep="last")
    return (
        latest.rename(columns={"status": "current_status", "updated_at": "last_updated_at"})[
            ["order_id", "customer_ref", "current_status", "ordered_at", "last_updated_at"]
        ]
        .sort_values("order_id")
        .reset_index(drop=True)
    )


def assert_status_never_regresses(orders: pd.DataFrame) -> str | None:
    """Gate: an order's status rank may never decrease as ``updated_at`` advances.

    Cancellation ranks highest because it is terminal; any move to a strictly
    lower rank after a higher one is a source-side regression that must block
    promotion of the collapsed state.
    """
    if orders.empty:
        return None
    ordered = orders.sort_values(["order_id", "updated_at"], kind="stable")
    previous_rank: dict[str, int] = {}
    for row in ordered.itertuples(index=False):
        rank = STATUS_RANK[row.status]
        seen = previous_rank.get(row.order_id)
        if seen is not None and rank < seen:
            return (
                f"status regression for {row.order_id}: {row.status} (rank {rank}) "
                f"after rank {seen} at updated_at={row.updated_at}"
            )
        previous_rank[row.order_id] = max(seen if seen is not None else rank, rank)
    return None


def _read_staged_orders() -> pd.DataFrame:
    from phlo_iceberg import get_catalog

    table = get_catalog(ref="main").load_table(SOURCE_TABLE)
    frame = table.scan().to_arrow().to_pandas()
    ShipmentOrderSchema.validate(frame)
    return frame


@dg.asset(
    name="order_current_state",
    deps=[dg.AssetKey("sling_shipments_orders")],
    group_name="orders_transforms",
    owners=["team:logistics-fulfillment"],
    metadata={"source_table": SOURCE_TABLE, "target_table": TARGET_TABLE},
    description=(
        "Collapse replicated order versions to one current row per order and "
        "block promotion when a status regresses backwards."
    ),
)
def order_current_state(context) -> None:
    """Materialize current order state into Iceberg after gating regressions."""
    staged = _read_staged_orders()
    violation = assert_status_never_regresses(staged)
    if violation:
        raise ValueError(f"orders status gate failed: {violation}")
    current = latest_order_versions(staged)
    PaDataFrame[OrderCurrentStateSchema](current)

    from phlo_iceberg import ensure_table, merge_to_table, pandera_to_iceberg

    ensure_table(
        TARGET_TABLE,
        pandera_to_iceberg(
            add_dlt_metadata=False, add_phlo_metadata=False, pandera_schema=OrderCurrentStateSchema
        ),
    )

    with tempfile.TemporaryDirectory() as scratch:
        data_path = Path(scratch) / "order_current_state.parquet"
        current.to_parquet(data_path, index=False)
        result = merge_to_table(TARGET_TABLE, data_path, unique_key="order_id")
    context.log.info(f"order_current_state merged {result['rows_inserted']} rows")
