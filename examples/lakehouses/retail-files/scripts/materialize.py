from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

from workflows.ingestion.retail.files import (
    read_historical_archive,
    read_inventory,
    read_reference,
    read_sales,
)
from workflows.quality.retail import validate_retail

ROOT = Path(__file__).resolve().parents[1]


def materialize(data: Path, database: Path) -> dict[str, float]:
    sales = read_sales(data)
    products = read_reference(data, "products")
    stores = read_reference(data, "stores")
    promotions = read_reference(data, "promotions")
    inventory = read_inventory(data)
    archive = read_historical_archive(data)
    validate_retail(sales, products, stores, promotions, inventory)
    con = duckdb.connect(str(database))
    try:
        con.execute("create schema if not exists raw")
        for name, frame in {
            "retail_sales_lines": pd_concat(sales, archive),
            "retail_products": products,
            "retail_stores": stores,
            "retail_promotions": promotions,
            "retail_inventory": inventory,
        }.items():
            con.register("incoming", frame)
            con.execute(f"create or replace table raw.{name} as select * from incoming")
        return dict(
            con.execute(
                "select count(*) as row_count, sum(gross_amount) as gross, "
                "sum(discount_amount) as discount, sum(tax_amount) as tax, "
                "sum(net_amount) as net from raw.retail_sales_lines"
            )
            .fetchdf()
            .iloc[0]
        )
    finally:
        con.close()


def pd_concat(sales, archive):
    import pandas as pd

    return pd.concat([sales, archive], ignore_index=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", type=Path, default=ROOT / "generated-data")
    p.add_argument("--database", type=Path, default=ROOT / "retail.duckdb")
    a = p.parse_args()
    print(materialize(a.data_dir, a.database))
