from __future__ import annotations

import json
from pathlib import Path

import dlt
import pandas as pd
import phlo
from phlo.contracts import SLA, Consumer

from workflows.schemas.retail import (
    InventorySchema,
    ProductsSchema,
    PromotionsSchema,
    SalesSchema,
    StoresSchema,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "generated-data"


def read_reference(data: Path, name: str) -> pd.DataFrame:
    return pd.DataFrame(json.loads((data / f"{name}.json").read_text(encoding="utf-8")))


def read_sales(data: Path) -> pd.DataFrame:
    stores = read_reference(data, "stores")
    expected = [
        (day.name, store.store_id)
        for day in sorted((data / "sales").iterdir())
        for store in stores.itertuples()
    ]
    missing = [
        f"{day}/{store}.csv"
        for day, store in expected
        if not (data / "sales" / day / f"{store}.csv").exists()
    ]
    if missing:
        raise FileNotFoundError(f"Missing required store files: {missing[:5]}")
    return pd.concat(
        [pd.read_csv(data / "sales" / day / f"{store}.csv") for day, store in expected],
        ignore_index=True,
    )


def read_sales_partition(data: Path, partition_date: str) -> pd.DataFrame:
    stores = read_reference(data, "stores")
    partition_dir = data / "sales" / partition_date
    missing = [
        f"{partition_date}/{store.store_id}.csv"
        for store in stores.itertuples()
        if not (partition_dir / f"{store.store_id}.csv").exists()
    ]
    if missing:
        raise FileNotFoundError(f"Missing required store files: {missing[:5]}")
    return pd.concat(
        [pd.read_csv(partition_dir / f"{store.store_id}.csv") for store in stores.itertuples()],
        ignore_index=True,
    )


def read_inventory(data: Path) -> pd.DataFrame:
    return pd.read_json(data / "inventory.ndjson", lines=True, convert_dates=False)


def read_historical_archive(data: Path) -> pd.DataFrame:
    return pd.read_parquet(data / "historical_sales.parquet")


@phlo.ingest.dlt(
    table_name="retail_sales_lines",
    unique_key="line_id",
    validation_schema=SalesSchema,
    group="retail",
    freshness_hours=(24, 30),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=1800,
    max_retries=3,
    retry_delay_seconds=60,
    add_metadata_columns=True,
    owner="retail-finance",
    consumers=[
        Consumer(name="finance", usage="daily revenue close"),
        Consumer(name="analytics", usage="store performance marts"),
    ],
    sla=SLA(freshness_hours=30, quality_threshold=1.0, notify=["retail-finance"]),
)
def retail_sales_lines(partition_date: str) -> object:
    return dlt.resource(
        read_sales_partition(DATA_DIR, partition_date).to_dict("records"),
        name="retail_sales_lines",
    )


@phlo.ingest.dlt(
    table_name="retail_products",
    unique_key="product_id",
    validation_schema=ProductsSchema,
    group="retail",
    freshness_hours=(72, 96),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=300,
    max_retries=1,
    retry_delay_seconds=30,
    add_metadata_columns=True,
    owner="retail-master-data",
    consumers=[Consumer(name="merchandising", usage="product hierarchy")],
    sla=SLA(freshness_hours=96, quality_threshold=1.0),
)
def retail_products(partition_date: str) -> object:
    del partition_date
    return dlt.resource(
        read_reference(DATA_DIR, "products").to_dict("records"),
        name="retail_products",
    )


@phlo.ingest.dlt(
    table_name="retail_stores",
    unique_key="store_id",
    validation_schema=StoresSchema,
    group="retail",
    freshness_hours=(168, 192),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=180,
    max_retries=1,
    retry_delay_seconds=30,
    add_metadata_columns=True,
    owner="retail-master-data",
    consumers=[Consumer(name="operations", usage="store hierarchy")],
    sla=SLA(freshness_hours=192, quality_threshold=1.0),
)
def retail_stores(partition_date: str) -> object:
    del partition_date
    return dlt.resource(
        read_reference(DATA_DIR, "stores").to_dict("records"),
        name="retail_stores",
    )


@phlo.ingest.dlt(
    table_name="retail_promotions",
    unique_key="promotion_id",
    validation_schema=PromotionsSchema,
    group="retail",
    freshness_hours=(24, 48),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=120,
    max_retries=2,
    retry_delay_seconds=20,
    add_metadata_columns=True,
    owner="retail-marketing",
    consumers=[Consumer(name="finance", usage="discount attribution")],
    sla=SLA(freshness_hours=48, quality_threshold=1.0),
)
def retail_promotions(partition_date: str) -> object:
    del partition_date
    return dlt.resource(
        read_reference(DATA_DIR, "promotions").to_dict("records"),
        name="retail_promotions",
    )


@phlo.ingest.dlt(
    table_name="retail_inventory",
    unique_key="inventory_snapshot_id",
    validation_schema=InventorySchema,
    group="retail",
    freshness_hours=(2, 4),
    merge_strategy="append",
    strict_validation=True,
    max_runtime_seconds=900,
    max_retries=5,
    retry_delay_seconds=30,
    add_metadata_columns=True,
    owner="retail-operations",
    consumers=[Consumer(name="operations", usage="replenishment decisions")],
    sla=SLA(freshness_hours=4, quality_threshold=1.0, notify=["retail-operations"]),
)
def retail_inventory(partition_date: str) -> object:
    return dlt.resource(
        read_inventory(DATA_DIR).query("partition_date == @partition_date").to_dict("records"),
        name="retail_inventory",
    )
