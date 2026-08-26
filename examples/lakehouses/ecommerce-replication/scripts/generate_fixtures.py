"""Deterministic commerce source fixtures.

Generates the initial state of a small commerce PostgreSQL source (customers,
products, orders, order lines, payments, config) plus an ``update`` scenario
whose rows are strictly newer than the base watermark. The update set is what
incremental replications must pick up without a full reload; the generator
fails loudly if any updated row violates that invariant.

All output is byte-stable for a given scale: no clocks, no randomness outside
a seeded generator.

Usage:
    python scripts/generate_fixtures.py                 # default scale, base only
    python scripts/generate_fixtures.py --scenario update
    python scripts/generate_fixtures.py --scale test    # pytest-sized
"""

import argparse
import csv
import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "generated-data"

BASE_START = date(2026, 7, 1)
BASE_DAYS = 14
WATERMARK = datetime(2026, 7, 15, 23, 0, tzinfo=UTC)
UPDATE_TS = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)

SEGMENTS = ("consumer", "business", "enterprise")
REGIONS = ("north", "south", "east", "west")
ORDER_STATUSES = ("pending", "shipped", "delivered", "cancelled")
PAYMENT_METHODS = ("card", "paypal", "bank")
CATEGORIES = (
    "electronics",
    "home",
    "kitchen",
    "sports",
    "books",
    "toys",
    "apparel",
    "garden",
)

SCALE = {
    "default": {"customers": 200, "products": 150, "orders_per_day": 100},
    "test": {"customers": 24, "products": 18, "orders_per_day": 8},
}


def _iso(day: date, offset_minutes: int) -> str:
    moment = datetime.combine(day, time(8, 0), tzinfo=UTC) + timedelta(
        minutes=offset_minutes % (12 * 60)
    )
    return moment.isoformat()


def _customer_rows(count: int) -> list[dict]:
    rows = []
    for n in range(1, count + 1):
        signup = BASE_START - timedelta(days=(n * 3) % 365)
        rows.append(
            {
                "customer_id": f"C{n:05d}",
                "email": f"customer{n:03d}@example.com",
                "full_name": f"Customer {n:03d}",
                "segment": SEGMENTS[n % len(SEGMENTS)],
                "region": REGIONS[n % len(REGIONS)],
                "signup_date": signup.isoformat(),
                "updated_at": _iso(BASE_START - timedelta(days=(n * 7) % 30), n * 11),
            }
        )
    return rows


def _product_rows(count: int) -> list[dict]:
    rows = []
    for n in range(1, count + 1):
        created = BASE_START - timedelta(days=(n * 5) % 180)
        rows.append(
            {
                "product_id": f"P{n:05d}",
                "sku": f"SKU-{n * 37:06d}",
                "name": f"Product {n:03d}",
                "category": CATEGORIES[n % len(CATEGORIES)],
                "unit_price": round(4.5 + (n * 0.37) % 90, 2),
                "active": n % 17 != 0,
                "created_at": _iso(created, n * 13),
                "updated_at": _iso(created, n * 29),
            }
        )
    return rows


def _order_and_child_rows(
    customers: int, products: int, orders_per_day: int
) -> tuple[list[dict], list[dict], list[dict]]:
    orders: list[dict] = []
    lines: list[dict] = []
    payments: list[dict] = []
    sequence = 0
    for day_offset in range(BASE_DAYS):
        day = BASE_START + timedelta(days=day_offset)
        for slot in range(orders_per_day):
            sequence += 1
            customer_n = ((day_offset * 7 + slot * 11) % customers) + 1
            if day_offset < BASE_DAYS - 4:
                status = ORDER_STATUSES[(day_offset + slot) % 3]  # delivered/shipped/pending mix
            else:
                status = ORDER_STATUSES[slot % 2]  # newest days: pending/shipped only
            ordered_at = _iso(day, slot * 9)
            order_id = f"O-202607{day.day:02d}-{slot + 1:04d}"
            line_count = slot % 3 + 1
            total = 0.0
            for line_no in range(1, line_count + 1):
                product_n = ((day_offset * 13 + slot * 5 + line_no * 3) % products) + 1
                quantity = 1 + (line_no + slot) % 5
                unit_price = round(4.5 + (product_n * 0.37) % 90, 2)
                amount = round(quantity * unit_price, 2)
                total += amount
                lines.append(
                    {
                        "order_id": order_id,
                        "line_id": f"L{line_no}",
                        "product_id": f"P{product_n:05d}",
                        "quantity": quantity,
                        "unit_price": unit_price,
                        "line_amount": amount,
                        "updated_at": ordered_at,
                    }
                )
            total = round(total, 2)
            orders.append(
                {
                    "order_id": order_id,
                    "customer_id": f"C{customer_n:05d}",
                    "status": status,
                    "currency": "USD",
                    "total_amount": total,
                    "ordered_at": ordered_at,
                    "updated_at": ordered_at,
                }
            )
            payment_specs = (
                [(total, 1)]
                if sequence % 9
                else [
                    (round(total * 0.6, 2), 1),
                    (round(total - round(total * 0.6, 2), 2), 2),
                ]
            )
            for amount, part in payment_specs:
                payments.append(
                    {
                        "payment_id": f"PAY-{sequence:06d}-{part}",
                        "order_id": order_id,
                        "method": PAYMENT_METHODS[(sequence + part) % 3],
                        "amount": amount,
                        "paid_at": _iso(day, slot * 9 + 45),
                        "updated_at": _iso(day, slot * 9 + 45),
                    }
                )
    return orders, lines, payments


def _config_rows() -> list[dict]:
    return [
        {"config_key": "currency", "config_value": "USD"},
        {"config_key": "tax_rate", "config_value": "0.0825"},
        {"config_key": "order_timeout_hours", "config_value": "48"},
        {"config_key": "replication_batch_size", "config_value": "5000"},
    ]


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        # No delta rows for this stream at this scale; omit the file rather
        # than write a column-less CSV.
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def generate(data_dir: Path = DEFAULT_DATA_DIR, scale_name: str = "default") -> dict[str, int]:
    """Write the base source state and return row counts per table."""
    scale = SCALE[scale_name]

    customers = _customer_rows(scale["customers"])
    products = _product_rows(scale["products"])
    orders, lines, payments = _order_and_child_rows(
        scale["customers"], scale["products"], scale["orders_per_day"]
    )
    config = _config_rows()

    tables = {
        "customers": customers,
        "products": products,
        "orders": orders,
        "order_lines": lines,
        "payments": payments,
        "commerce_config": config,
    }
    for name, rows in tables.items():
        _write_csv(data_dir / "base" / f"{name}.csv", rows)

    counts = {name: len(rows) for name, rows in tables.items()}
    counts["scale_name"] = scale_name
    return counts


def build_update_set(
    data_dir: Path = DEFAULT_DATA_DIR, scale_name: str = "default"
) -> dict[str, int]:
    """Derive strictly-newer delta rows from the base state.

    Emits ``update/*.csv`` holding only changed or new rows. Every emitted
    ``updated_at`` is greater than the base watermark; the function raises if
    that invariant is broken, because incremental replication correctness in
    this example depends on it.
    """
    scale = SCALE[scale_name]
    base_dir = data_dir / "base"
    update_dir = data_dir / "update"
    update_dir.mkdir(parents=True, exist_ok=True)

    def read(name: str) -> list[dict]:
        with (base_dir / f"{name}.csv").open(encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    customers = read("customers")
    orders = read("orders")
    payments = read("payments")

    bumped = UPDATE_TS.isoformat()
    assert bumped > WATERMARK.isoformat(), "update timestamp must exceed the base watermark"

    customer_updates = []
    for index, row in enumerate(customers):
        if index % 8 == 0:
            revised = dict(row)
            revised["segment"] = SEGMENTS[(SEGMENTS.index(row["segment"]) + 1) % len(SEGMENTS)]
            revised["updated_at"] = bumped
            customer_updates.append(revised)

    order_updates = []
    for index, row in enumerate(orders):
        if index % 25 == 0:
            revised = dict(row)
            next_status = ORDER_STATUSES[min(ORDER_STATUSES.index(row["status"]) + 1, 2)]
            revised["status"] = next_status
            revised["updated_at"] = bumped
            order_updates.append(revised)

    payment_updates = []
    for index, row in enumerate(payments):
        if index % 200 == 0 and index > 0:
            revised = dict(row)
            revised["method"] = PAYMENT_METHODS[(PAYMENT_METHODS.index(row["method"]) + 1) % 3]
            revised["updated_at"] = bumped
            payment_updates.append(revised)

    new_orders, new_lines, new_payments = [], [], []
    for new_slot in range(scale["orders_per_day"] // 2):
        sequence = 10_000 + new_slot
        customer_n = ((new_slot * 17) % scale["customers"]) + 1
        ordered_at = UPDATE_TS.isoformat()
        order_id = f"O-20260720-{new_slot + 5001:04d}"
        total = 0.0
        for line_no in range(1, new_slot % 3 + 2):
            product_n = ((new_slot * 19 + line_no * 7) % scale["products"]) + 1
            quantity = 1 + (line_no + new_slot) % 4
            unit_price = round(4.5 + (product_n * 0.37) % 90, 2)
            amount = round(quantity * unit_price, 2)
            total += amount
            new_lines.append(
                {
                    "order_id": order_id,
                    "line_id": f"L{line_no}",
                    "product_id": f"P{product_n:05d}",
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "line_amount": amount,
                    "updated_at": ordered_at,
                }
            )
        total = round(total, 2)
        new_orders.append(
            {
                "order_id": order_id,
                "customer_id": f"C{customer_n:05d}",
                "status": "pending",
                "currency": "USD",
                "total_amount": total,
                "ordered_at": ordered_at,
                "updated_at": ordered_at,
            }
        )
        new_payments.append(
            {
                "payment_id": f"PAY-{sequence:06d}-1",
                "order_id": order_id,
                "method": PAYMENT_METHODS[new_slot % 3],
                "amount": total,
                "paid_at": ordered_at,
                "updated_at": ordered_at,
            }
        )

    for name, rows in {
        "customers": customer_updates,
        "orders": order_updates,
        "payments": payment_updates,
        "new_orders": new_orders,
        "order_lines": new_lines,
        "new_payments": new_payments,
    }.items():
        _write_csv(update_dir / f"{name}.csv", rows)

    _validate_watermarks(base_dir, update_dir)
    counts = {
        name: len(rows)
        for name, rows in {
            "customers": customer_updates,
            "orders": order_updates,
            "payments": payment_updates,
            "new_orders": new_orders,
            "order_lines": new_lines,
            "new_payments": new_payments,
        }.items()
    }
    return counts


def write_failure_fixtures(data_dir: Path = DEFAULT_DATA_DIR) -> None:
    """Write labeled invalid rows used to prove checks fail on bad input."""
    failures = data_dir / "failures"
    _write_json(
        failures / "orphan_order_line.json",
        [
            {
                "order_id": "O-DOES-NOT-EXIST",
                "line_id": "L1",
                "product_id": "P00001",
                "quantity": 1,
                "unit_price": 10.0,
                "line_amount": 10.0,
                "updated_at": UPDATE_TS.isoformat(),
            }
        ],
    )
    _write_json(
        failures / "over_payment.json",
        [
            {
                "payment_id": "PAY-BAD-000001-1",
                "order_id": "O-20260701-0001",
                "method": "card",
                "amount": 999999.99,
                "paid_at": UPDATE_TS.isoformat(),
                "updated_at": UPDATE_TS.isoformat(),
            }
        ],
    )
    _write_json(
        failures / "stale_customer.json",
        [
            {
                "customer_id": "C00001",
                "email": "stale@example.com",
                "full_name": "Stale Customer",
                "segment": "consumer",
                "region": "north",
                "signup_date": BASE_START.isoformat(),
                # Older than every replicated row: must never advance the watermark.
                "updated_at": "2026-06-01T00:00:00+00:00",
            }
        ],
    )


def _read_column(path: Path, column: str) -> list[str]:
    with path.open(encoding="utf-8") as handle:
        return [row[column] for row in csv.DictReader(handle)]


def _validate_watermarks(base_dir: Path, update_dir: Path) -> None:
    """Fail if any delta row could regress a stream's incremental watermark."""
    checks = [
        ("customers.csv", "customers.csv"),
        ("orders.csv", "orders.csv"),
        ("payments.csv", "payments.csv"),
    ]
    for base_name, update_name in checks:
        update_path = update_dir / update_name
        if not update_path.exists():
            continue
        base_values = _read_column(base_dir / base_name, "updated_at")
        update_values = _read_column(update_path, "updated_at")
        if not update_values:
            continue
        if min(update_values) <= max(base_values):
            raise ValueError(
                f"update rows for {update_name} are not strictly newer than the base watermark"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--scale", choices=sorted(SCALE), default="default")
    parser.add_argument("--scenario", choices=["none", "update"], default="none")
    args = parser.parse_args()
    counts = generate(args.data_dir, args.scale)
    print(f"base fixtures written to {args.data_dir / 'base'}: {counts}")
    if args.scenario == "update":
        deltas = build_update_set(args.data_dir, args.scale)
        print(f"update fixtures written to {args.data_dir / 'update'}: {deltas}")
    write_failure_fixtures(args.data_dir)
