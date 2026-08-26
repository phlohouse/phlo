"""Deterministically generate production-shaped local retail drops."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCALES = {"test": (2, 10, 2, 40), "default": (25, 500, 30, 80)}


def generate(data: Path, scale: str = "default") -> dict[str, int]:
    stores_n, products_n, days_n, lines_per_store_day = SCALES[scale]
    if data.exists():
        shutil.rmtree(data)
    data.mkdir(parents=True)
    stores = [
        {
            "store_id": f"S{i:03}",
            "store_name": f"Retail Store {i:03}",
            "region": ("north", "south", "east", "west")[i % 4],
            "format": ("urban", "mall", "outlet")[i % 3],
            "timezone": "America/New_York",
            "open_date": "2018-01-01",
        }
        for i in range(1, stores_n + 1)
    ]
    products = [
        {
            "product_id": f"P{i:04}",
            "product_name": f"Product {i:04}",
            "category": f"category-{i % 12}",
            "subcategory": f"subcategory-{i % 40}",
            "brand": f"brand-{i % 20}",
            "supplier_id": f"SUP{i % 15:02}",
            "unit_cost": float(2 + i % 40),
            "list_price": round(5 + (i % 40) * 1.8, 2),
            "active": True,
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
        }
        for i in range(1, products_n + 1)
    ]
    promotions = [
        {"promotion_id": "PROMO10", "promotion_name": "Ten percent", "discount_rate": 0.1},
        {"promotion_id": "PROMO20", "promotion_name": "Twenty percent", "discount_rate": 0.2},
    ]
    (data / "stores.json").write_text(json.dumps(stores), encoding="utf-8")
    (data / "products.json").write_text(json.dumps(products), encoding="utf-8")
    (data / "promotions.json").write_text(json.dumps(promotions), encoding="utf-8")
    start = date(2025, 1, 1)
    inventory_rows: list[dict[str, object]] = []
    sales_rows: list[dict[str, object]] = []
    file_count = 0
    for day_offset in range(days_n):
        partition = (start + timedelta(days=day_offset)).isoformat()
        day_dir = data / "sales" / partition
        day_dir.mkdir(parents=True)
        for store_index, store in enumerate(stores):
            rows = []
            for line in range(lines_per_store_day):
                product = products[(line * 17 + store_index * 7 + day_offset) % products_n]
                transaction = f"T{day_offset:02}{store_index:02}{line // 2:03}"
                quantity = 1 + line % 3
                list_price = product["list_price"]
                promotion_id = "PROMO10" if line % 10 == 0 else None
                discount = round(list_price * quantity * (0.1 if promotion_id else 0), 2)
                gross = round(list_price * quantity, 2)
                tax = round((gross - discount) * 0.08, 2)
                is_return = line % 37 == 0
                sign = -1 if is_return else 1
                rows.append(
                    {
                        "transaction_id": transaction,
                        "line_id": f"{transaction}-{line % 2 + 1}",
                        "store_id": store["store_id"],
                        "register_id": f"R{line % 6 + 1}",
                        "cashier_id": f"C{store_index % 12:02}",
                        "customer_id": f"CU{(line + day_offset) % 2000:04}",
                        "product_id": product["product_id"],
                        "sold_at": f"{partition}T{8 + line % 11:02}:{line % 60:02}:00Z",
                        "quantity": sign * quantity,
                        "list_price": list_price,
                        "unit_price": list_price,
                        "gross_amount": sign * gross,
                        "discount_amount": sign * discount,
                        "tax_amount": sign * tax,
                        "net_amount": sign * round(gross - discount + tax, 2),
                        "currency": "USD",
                        "payment_method": ("card", "cash", "wallet")[line % 3],
                        "channel": "store",
                        "promotion_id": promotion_id,
                        "is_return": is_return,
                        "partition_date": partition,
                    }
                )
            pd.DataFrame(rows).to_csv(day_dir / f"{store['store_id']}.csv", index=False)
            sales_rows.extend(rows)
            file_count += 1
            for product in products:
                on_hand = 20 + (store_index + int(product["product_id"][1:]) + day_offset) % 80
                observed_at = f"{partition}T23:00:00Z"
                inventory_rows.append(
                    {
                        "inventory_snapshot_id": (
                            f"I-{store['store_id']}-{product['product_id']}-{partition}"
                        ),
                        "store_id": store["store_id"],
                        "product_id": product["product_id"],
                        "observed_at": observed_at,
                        "on_hand": on_hand,
                        "reserved": on_hand % 5,
                        "in_transit": on_hand % 7,
                        "reorder_point": 25,
                        "safety_stock": 10,
                        "partition_date": partition,
                    }
                )
    with (data / "inventory.ndjson").open("w", encoding="utf-8") as handle:
        for row in inventory_rows:
            handle.write(json.dumps(row) + "\n")
    archive = pd.DataFrame(sales_rows[: min(1000, len(sales_rows))]).copy()
    archive["transaction_id"] = "H" + archive["transaction_id"]
    archive["line_id"] = "H" + archive["line_id"]
    archive["sold_at"] = archive["sold_at"].str.replace("2025-01-01", "2024-12-31")
    archive["partition_date"] = "2024-12-31"
    archive.to_parquet(data / "historical_sales.parquet", index=False)
    failures = data / "failures"
    failures.mkdir()
    pd.DataFrame([sales_rows[0], sales_rows[0]]).to_csv(
        failures / "duplicate_line.csv", index=False
    )
    bad_ref = dict(sales_rows[1])
    bad_ref["product_id"] = "UNKNOWN"
    pd.DataFrame([bad_ref]).to_csv(failures / "unknown_product.csv", index=False)
    bad_math = dict(sales_rows[2])
    bad_math["net_amount"] = 999
    pd.DataFrame([bad_math]).to_csv(failures / "bad_arithmetic.csv", index=False)
    (failures / "malformed.ndjson").write_text('{"broken":\n', encoding="utf-8")
    return {
        "stores": stores_n,
        "products": products_n,
        "dates": days_n,
        "sales_rows": len(sales_rows),
        "sales_files": file_count,
        "inventory_rows": len(inventory_rows),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", choices=SCALES, default="default")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "generated-data")
    args = parser.parse_args()
    print(generate(args.data_dir, args.scale))
