"""Carriers-domain Python transforms: feed unification, exceptions, coverage.

This is one of the four repeated Python transform folders exercised by this
example; every ``@dg.asset`` here is discovered by the framework directly from
the workflow module.

Asset-name collision resolution
    Both this domain and the warehouses domain naturally produce a per-shipment
    exception view, and both initially registered an asset named
    ``shipment_exceptions``. The framework merges definitions from all workflow
    modules, so duplicate asset keys would collide. The name stays here because
    carrier-reported exceptions are the operational source of truth that the
    control tower must react to; the warehouses domain renamed its physical
    observation view to ``warehouse_scan_exceptions``. See
    ``workflows/warehouses/transforms/scan_metrics.py`` for the other half.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import dagster as dg
import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series

from workflows.schemas.logistics import CarrierEventSchema

SOURCE_TABLES = ("raw.carrier_events_atlas", "raw.carrier_events_corsair")
UNIFIED_TABLE = "raw.carrier_events"
EXCEPTIONS_TABLE = "raw.shipment_exceptions"
COVERAGE_TABLE = "raw.carrier_coverage"


class ShipmentExceptionSchema(pa.DataFrameModel):
    """One row per shipment whose latest carrier state is an exception."""

    shipment_id: Series[str] = pa.Field(unique=True)
    carrier: Series[str]
    exception_time: Series[datetime]
    location: Series[str]
    recovered: Series[bool]

    class Config:
        strict = False
        coerce = True


class CarrierCoverageSchema(pa.DataFrameModel):
    """Event-volume and shipment coverage per carrier over the fixture window."""

    carrier: Series[str] = pa.Field(unique=True)
    event_count: Series[int] = pa.Field(ge=1)
    distinct_shipments: Series[int] = pa.Field(ge=1)
    first_event_time: Series[datetime]
    last_event_time: Series[datetime]

    class Config:
        strict = False
        coerce = True


def normalize_carrier_events(atlas: pd.DataFrame, corsair: pd.DataFrame) -> pd.DataFrame:
    """Union both carrier feeds into one stream deduplicated on event_id."""
    unified = pd.concat([atlas, corsair], ignore_index=True)
    unified = unified.drop_duplicates(subset="event_id", keep="first")
    return unified.sort_values(["event_time", "event_id"], kind="stable").reset_index(drop=True)


def build_shipment_exceptions(events: pd.DataFrame) -> pd.DataFrame:
    """Latest-state-is-exception rows, flagged whether delivery followed later."""
    ordered = events.sort_values(["shipment_id", "event_time"], kind="stable")
    rows: list[dict[str, object]] = []
    for shipment_id, group in ordered.groupby("shipment_id"):
        last = group.iloc[-1]
        if last["event_type"] != "exception":
            continue
        later_delivery = group[
            (group["event_type"] == "delivered") & (group["event_time"] > last["event_time"])
        ]
        rows.append(
            {
                "shipment_id": shipment_id,
                "carrier": last["carrier"],
                "exception_time": last["event_time"],
                "location": last["location"],
                "recovered": bool(len(later_delivery)),
            }
        )
    if not rows:
        return pd.DataFrame(
            columns=["shipment_id", "carrier", "exception_time", "location", "recovered"]
        )
    return pd.DataFrame(rows).sort_values("shipment_id").reset_index(drop=True)


def compute_carrier_coverage(events: pd.DataFrame) -> pd.DataFrame:
    """Aggregate event volume and distinct shipments per carrier."""
    grouped = events.groupby("carrier", sort=True)
    coverage = grouped.agg(
        event_count=("event_id", "count"),
        distinct_shipments=("shipment_id", "nunique"),
        first_event_time=("event_time", "min"),
        last_event_time=("event_time", "max"),
    )
    return coverage.reset_index()


def _read_staged(table: str) -> pd.DataFrame:
    from phlo_iceberg import get_catalog

    return get_catalog(ref="main").load_table(table).scan().to_arrow().to_pandas()


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
    name="carrier_events_unified",
    deps=[
        dg.AssetKey("dlt_carrier_events_atlas"),
        dg.AssetKey("dlt_carrier_events_corsair"),
    ],
    group_name="carriers_transforms",
    owners=["team:logistics-carrier-ops"],
    metadata={"source_tables": list(SOURCE_TABLES), "target_table": UNIFIED_TABLE},
    description="Union both carrier feeds into one deduplicated event stream.",
)
def carrier_events_unified(context) -> None:
    """Merge the per-carrier staging tables into raw.carrier_events on event_id."""
    unified = normalize_carrier_events(
        _read_staged(SOURCE_TABLES[0]), _read_staged(SOURCE_TABLES[1])
    )
    CarrierEventSchema.validate(unified)
    result = _write_table(unified, CarrierEventSchema, UNIFIED_TABLE, "event_id")
    context.log.info(f"carrier_events_unified merged {result['rows_inserted']} rows")


@dg.asset(
    name="shipment_exceptions",
    deps=[dg.AssetKey("carrier_events_unified")],
    group_name="carriers_transforms",
    owners=["team:logistics-carrier-ops"],
    metadata={"target_table": EXCEPTIONS_TABLE},
    description="Shipments whose latest carrier state is an exception, with recovery flag.",
)
def shipment_exceptions(context) -> None:
    """Derive the operational exception queue from the unified carrier stream."""
    exceptions = build_shipment_exceptions(_read_staged(UNIFIED_TABLE))
    ShipmentExceptionSchema.validate(exceptions)
    result = _write_table(exceptions, ShipmentExceptionSchema, EXCEPTIONS_TABLE, "shipment_id")
    context.log.info(f"shipment_exceptions merged {result['rows_inserted']} rows")


@dg.asset(
    name="carrier_coverage",
    deps=[dg.AssetKey("carrier_events_unified")],
    group_name="carriers_transforms",
    owners=["team:logistics-carrier-ops"],
    metadata={"target_table": COVERAGE_TABLE},
    description="Per-carrier event volume, distinct shipments, and observed window.",
)
def carrier_coverage(context) -> None:
    """Quantify how much of the network each carrier's feed actually covers."""
    coverage = compute_carrier_coverage(_read_staged(UNIFIED_TABLE))
    CarrierCoverageSchema.validate(coverage)
    result = _write_table(coverage, CarrierCoverageSchema, COVERAGE_TABLE, "carrier")
    context.log.info(f"carrier_coverage merged {result['rows_inserted']} rows")
