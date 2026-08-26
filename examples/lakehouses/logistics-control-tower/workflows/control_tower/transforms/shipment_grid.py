"""Control-tower Python transform: the cross-domain shipment grid.

This is one of the four repeated Python transform folders exercised by this
example. The asset below is where all three upstream domains meet: it declares
explicit cross-folder dependencies on the orders transform output
(``order_current_state``), the carriers transform outputs (``carrier_events_unified``
and ``shipment_exceptions``), and the warehouses transform output
(``warehouse_dwell``), converging them into one wide shipment grid that the
control-tower dbt project extends with canonical state, transit duration, and
the SLA mart.

Write path: results land in Iceberg through the phlo_iceberg helpers
(``ensure_table`` + ``pandera_to_iceberg`` schema contract, ``merge_to_table``
upsert on ``shipment_id``).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import dagster as dg
import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series

GRID_TABLE = "raw.control_tower_shipment_grid"


class ShipmentGridSchema(pa.DataFrameModel):
    """One row per shipment with order, carrier, exception, and dwell signals."""

    shipment_id: Series[str] = pa.Field(unique=True)
    order_id: Series[str]
    customer_ref: Series[str]
    order_status: Series[str]
    carriers_seen: Series[int] = pa.Field(ge=1)
    has_exception: Series[bool]
    dwell_hours: Series[float]

    class Config:
        strict = False
        coerce = True


def _read_staged(table: str):
    from phlo_iceberg import get_catalog

    return get_catalog(ref="main").load_table(table).scan().to_arrow().to_pandas()


def build_shipment_grid(
    order_state,
    events,
    exceptions,
    dwell,
) -> pd.DataFrame:
    """Join the three domain outputs on their shared shipment/order keys.

    Orders carry ``order_id``; carrier events and warehouse scans carry
    ``shipment_id``. The fixture generator maps shipment ``SHP-200n`` to order
    ``ORD-(n-1000)`` (SHP-2001 -> ORD-1001), so the control tower can converge
    the domains without any of them leaking each other's keys.

    Missing dwell is left as NaN so the Pandera contract fails loudly instead
    of hiding an unscanned shipment behind a sentinel value.
    """

    shipment_ids = set(events["shipment_id"]) | set(dwell["shipment_id"])
    grid = pd.DataFrame({"shipment_id": sorted(shipment_ids)})
    grid["order_id"] = [f"ORD-{int(sid.split('-')[1]) - 1000}" for sid in grid["shipment_id"]]
    orders = order_state.rename(columns={"current_status": "order_status", "order_id": "order_key"})
    grid = grid.merge(
        orders[["order_key", "customer_ref", "order_status"]],
        left_on="order_id",
        right_on="order_key",
        how="left",
    ).drop(columns=["order_key"])
    grid["carriers_seen"] = (
        events.groupby("shipment_id")["carrier"]
        .nunique()
        .reindex(grid["shipment_id"])
        .fillna(0)
        .astype(int)
        .to_numpy()
    )
    grid["has_exception"] = grid["shipment_id"].isin(set(exceptions["shipment_id"]))
    dwell = dwell[["shipment_id", "dwell_hours"]]
    grid = grid.merge(dwell, on="shipment_id", how="left")
    return grid.sort_values("shipment_id").reset_index(drop=True)


@dg.asset(
    name="control_tower_shipment_grid",
    deps=[
        dg.AssetKey("order_current_state"),
        dg.AssetKey("carrier_events_unified"),
        dg.AssetKey("shipment_exceptions"),
        dg.AssetKey("warehouse_dwell"),
    ],
    group_name="control_tower_transforms",
    owners=["team:logistics-control-tower"],
    metadata={"target_table": GRID_TABLE},
    description=(
        "Converge order state (orders domain), carrier exceptions (carriers "
        "domain), and physical dwell (warehouses domain) into one grid."
    ),
)
def control_tower_shipment_grid(context) -> None:
    """Materialize the converged grid into Iceberg for the dbt marts."""
    grid = build_shipment_grid(
        _read_staged("raw.order_current_state"),
        _read_staged("raw.carrier_events"),
        _read_staged("raw.shipment_exceptions"),
        _read_staged("raw.warehouse_dwell"),
    )
    ShipmentGridSchema.validate(grid)

    from phlo_iceberg import ensure_table, merge_to_table, pandera_to_iceberg

    ensure_table(
        GRID_TABLE,
        pandera_to_iceberg(
            add_dlt_metadata=False, add_phlo_metadata=False, pandera_schema=ShipmentGridSchema
        ),
    )

    with tempfile.TemporaryDirectory() as scratch:
        data_path = Path(scratch) / "shipment_grid.parquet"
        grid.to_parquet(data_path, index=False)
        result = merge_to_table(GRID_TABLE, data_path, unique_key="shipment_id")
        context.log.info(f"control_tower_shipment_grid merged {result['rows_inserted']} rows")
