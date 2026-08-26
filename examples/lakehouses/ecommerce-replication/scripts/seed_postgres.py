"""Seed the local commerce PostgreSQL source from generated fixtures.

The database runs via ``docker compose up -d commerce-postgres``. Stages:

- ``base``   destructive reload of the initial state.
- ``update`` idempotent upsert of the delta rows produced with
             ``generate_fixtures.py --scenario update``. This mimics source-side
             inserts/updates; incremental replications should pick up exactly
             these rows on their next run.

Usage:
    python scripts/seed_postgres.py --stage base
    python scripts/seed_postgres.py --stage update
"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "generated-data"
DEFAULT_URL = "postgresql://commerce:commerce@localhost:5436/commerce"

TABLE_COLUMNS = {
    "customers": {
        "customer_id": "varchar(16)",
        "email": "varchar(320)",
        "full_name": "varchar(200)",
        "segment": "varchar(16)",
        "region": "varchar(16)",
        "signup_date": "date",
        "updated_at": "timestamptz",
    },
    "products": {
        "product_id": "varchar(16)",
        "sku": "varchar(24)",
        "name": "varchar(200)",
        "category": "varchar(32)",
        "unit_price": "numeric(10,2)",
        "active": "boolean",
        "created_at": "timestamptz",
        "updated_at": "timestamptz",
    },
    "orders": {
        "order_id": "varchar(24)",
        "customer_id": "varchar(16)",
        "status": "varchar(16)",
        "currency": "varchar(3)",
        "total_amount": "numeric(12,2)",
        "ordered_at": "timestamptz",
        "updated_at": "timestamptz",
    },
    "order_lines": {
        "order_id": "varchar(24)",
        "line_id": "varchar(8)",
        "product_id": "varchar(16)",
        "quantity": "integer",
        "unit_price": "numeric(10,2)",
        "line_amount": "numeric(12,2)",
        "updated_at": "timestamptz",
    },
    "payments": {
        "payment_id": "varchar(24)",
        "order_id": "varchar(24)",
        "method": "varchar(16)",
        "amount": "numeric(12,2)",
        "paid_at": "timestamptz",
        "updated_at": "timestamptz",
    },
    "commerce_config": {
        "config_key": "varchar(64)",
        "config_value": "varchar(255)",
    },
}

# Primary key per table, mirroring what the Sling streams declare.
TABLE_KEYS = {
    "customers": ["customer_id"],
    "products": ["product_id"],
    "orders": ["order_id"],
    "order_lines": ["order_id", "line_id"],
    "payments": ["payment_id"],
    "commerce_config": ["config_key"],
}

DELTA_UPSERTS = {
    # table -> delta CSV holding changed rows (keyed upserts)
    "customers": "customers.csv",
    "orders": "orders.csv",
    "payments": "payments.csv",
}

DELTA_INSERTS = {
    # delta CSV -> (target table)
    "new_orders.csv": "orders",
    "new_payments.csv": "payments",
    "order_lines.csv": "order_lines",
}


def _connect(url: str):
    return psycopg2.connect(url)


def _load_rows(path: Path) -> tuple[list[str], list[tuple]]:
    with path.open(encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        return header, [tuple(row) for row in reader]


def seed_base(url: str, data_dir: Path) -> None:
    with _connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("drop schema if exists public cascade")
            cur.execute("create schema public")
            for table, columns in TABLE_COLUMNS.items():
                header, rows = _load_rows(data_dir / "base" / f"{table}.csv")
                column_list = ", ".join(header)
                placeholders = ", ".join(["%s"] * len(header))
                ddl = ", ".join(f"{col} {columns[col]}" for col in header)
                cur.execute(f"create table {table} ({ddl})")
                cur.executemany(
                    f"insert into {table} ({column_list}) values ({placeholders})", rows
                )
            for table, keys in TABLE_KEYS.items():
                constraint = f"_{table}_pk"
                cur.execute(
                    f"alter table {table} add constraint {constraint} "
                    f"primary key ({', '.join(keys)})"
                )


def seed_update(url: str, data_dir: Path) -> dict[str, int]:
    applied: dict[str, int] = {}
    with _connect(url) as conn:
        with conn.cursor() as cur:
            for table, file_name in DELTA_UPSERTS.items():
                applied[file_name] = _upsert(cur, data_dir / "update" / file_name, table)
            for file_name, table in DELTA_INSERTS.items():
                applied[file_name] = _upsert(cur, data_dir / "update" / file_name, table)
    return applied


def _upsert(cur, path: Path, table: str) -> int:
    """Upsert one delta CSV into ``table``; returns rows applied (0 if absent)."""
    if not path.exists():
        return 0
    header, rows = _load_rows(path)
    keys = TABLE_KEYS[table]
    columns = ", ".join(header)
    placeholders = ", ".join(["%s"] * len(header))
    key_clause = ", ".join(keys)
    updates = ", ".join(f"{col} = excluded.{col}" for col in header if col not in keys)
    statement = (
        f"insert into {table} ({columns}) values ({placeholders}) "
        f"on conflict ({key_clause}) do update{' set ' + updates if updates else ' nothing'}"
    )
    cur.executemany(statement, rows)
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["base", "update"], required=True)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--url", default=os.environ.get("COMMERCE_SOURCE_URL", DEFAULT_URL))
    args = parser.parse_args()
    if args.stage == "base":
        seed_base(args.url, args.data_dir)
        print(f"seeded base state into {args.url.split('@')[-1]}")
    else:
        applied = seed_update(args.url, args.data_dir)
        print(f"applied delta rows into {args.url.split('@')[-1]}: {applied}")
