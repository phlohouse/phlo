"""Warehouses-domain Python transforms: dwell time and scan anomalies.

This is one of the four repeated Python transform folders exercised by this
example; every ``@dg.asset`` here is discovered by the framework directly from
the workflow module.

Asset-name collision resolution
    This domain's exception view was originally registered as
    ``shipment_exceptions``, colliding with the carriers domain asset of the
    same name (the framework merges definitions from all workflow modules, so
    duplicate asset keys are a hard error). It is renamed to
    ``warehouse_scan_exceptions`` because the carriers feed is the operational
    source of truth for shipment exceptions; warehouse scans contribute the
    physical-observation view and keep their domain prefix. See
    ``workflows/carriers/transforms/carrier_metrics.py`` for the other half.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import dagster as dg
import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series

SOURCE_TABLE = "raw.warehouse_scans"
DWELL_TABLE = "raw.warehouse_dwell"
ANOMALY_TABLE = "raw.warehouse_scan_exceptions"


class WarehouseDwellSchema(pa.DataFrameModel):
    """Inbound-to-outbound dwell per shipment and warehouse, in hours."""

    shipment_id: Series[str] = pa.Field(unique=True)
    warehouse_id: Series[str]
    inbound_at: Series[datetime]
    outbound_at: Series[datetime]
    dwell_hours: Series[float] = pa.Field(ge=0.0)

    class Config:
        strict = False
        coerce = True


class WarehouseScanAnomalySchema(pa.DataFrameModel):
    """Shipments that entered a warehouse but never scanned out."""

    shipment_id: Series[str] = pa.Field(unique=True)
    warehouse_id: Series[str]
    inbound_at: Series[datetime]

    class Config:
        strict = False
        coerce = True


def compute_dwell(scans: pd.DataFrame) -> pd.DataFrame:
    """Pair inbound/outbound scans per shipment; dwell hours between them.

    Shipments without an outbound scan are excluded here and surfaced by the
    anomaly view instead, so each invariant has exactly one owner.
    """
    pivoted = scans.pivot_table(
        index=["shipment_id", "warehouse_id"],
        columns="scan_type",
        values="scanned_at",
        aggfunc="first",
        dropna=False,
    ).reset_index()
    paired = pivoted.dropna(subset=["inbound", "outbound"])
    if paired.empty:
        return pd.DataFrame(
            columns=["shipment_id", "warehouse_id", "inbound_at", "outbound_at", "dwell_hours"]
        )
    result = pd.DataFrame(
        {
            "shipment_id": paired["shipment_id"],
            "warehouse_id": paired["warehouse_id"],
            "inbound_at": paired["inbound"],
            "outbound_at": paired["outbound"],
        }
    )
    result["dwell_hours"] = (
        pd.to_datetime(result["outbound_at"]) - pd.to_datetime(result["inbound_at"])
    ).dt.total_seconds() / 3600.0
    return result.sort_values("shipment_id").reset_index(drop=True)


def build_scan_anomalies(scans: pd.DataFrame) -> pd.DataFrame:
    """Shipments with an inbound scan but no matching outbound scan."""
    inbound = scans[scans["scan_type"] == "inbound"].set_index("shipment_id")
    outbound_ids = set(scans.loc[scans["scan_type"] == "outbound", "shipment_id"])
    open_shipments = inbound.drop(index=[s for s in inbound.index if s in outbound_ids])
    if open_shipments.empty:
        return pd.DataFrame(columns=["shipment_id", "warehouse_id", "inbound_at"])
    anomalies = (
        open_shipments.rename(columns={"scanned_at": "inbound_at"})
        .reset_index()[["shipment_id", "warehouse_id", "inbound_at"]]
        .sort_values("shipment_id")
        .reset_index(drop=True)
    )
    return anomalies


def _read_staged() -> pd.DataFrame:
    from phlo_iceberg import get_catalog

    return get_catalog(ref="main").load_table(SOURCE_TABLE).scan().to_arrow().to_pandas()


def _write_table(frame: pd.DataFrame, model, table_name: str, unique_key: str) -> dict[str, int]:
    from phlo_iceberg import ensure_table, merge_to_table, pandera_to_iceberg

    ensure_table(
        table_name,
        pandera_to_iceberg(add_dlt_metadata=False, add_phlo_metadata=False, pandera_schema=model),
    )
    with tempfile.TemporaryDirectory() as scratch:
        data_path = Path(scratch) / f"{table_name.replace('.', '_')}.parquet"
        frame.to_parquet(data_path, index=False)
        return merge_to_table(table_name, data_path, unique_key=unique_key)


@dg.asset(
    name="warehouse_dwell",
    deps=[dg.AssetKey("dlt_warehouse_scans")],
    group_name="warehouses_transforms",
    owners=["team:logistics-warehouse-ops"],
    metadata={"source_table": SOURCE_TABLE, "target_table": DWELL_TABLE},
    description="Paired inbound/outbound dwell hours per shipment and warehouse.",
)
def warehouse_dwell(context) -> None:
    """Materialize physical dwell times from merged warehouse scans."""
    dwell = compute_dwell(_read_staged())
    WarehouseDwellSchema.validate(dwell)
    result = _write_table(dwell, WarehouseDwellSchema, DWELL_TABLE, "shipment_id")
    context.log.info(f"warehouse_dwell merged {result['rows_inserted']} rows")


@dg.asset(
    name="warehouse_scan_exceptions",
    deps=[dg.AssetKey("dlt_warehouse_scans")],
    group_name="warehouses_transforms",
    owners=["team:logistics-warehouse-ops"],
    metadata={"target_table": ANOMALY_TABLE},
    description="Shipments that entered a warehouse but never scanned out.",
)
def warehouse_scan_exceptions(context) -> None:
    """Surface unclosed dwells as physical-observation anomalies."""
    anomalies = build_scan_anomalies(_read_staged())
    WarehouseScanAnomalySchema.validate(anomalies)
    result = _write_table(anomalies, WarehouseScanAnomalySchema, ANOMALY_TABLE, "shipment_id")
    context.log.info(f"warehouse_scan_exceptions merged {result['rows_inserted']} rows")
