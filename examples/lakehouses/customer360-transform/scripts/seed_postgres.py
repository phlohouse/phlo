"""Seed the local commerce PostgreSQL source from generated fixtures.

Run after ``docker compose up``:

    uv run python scripts/seed_postgres.py            # base state
    uv run python scripts/seed_postgres.py --update   # apply the delta set

The base load is idempotent (upserts by natural key), and every update row is
watermark-newer so incremental replication picks it up.
"""

import argparse
import csv
import json
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "generated-data"
DEFAULT_URL = "postgresql://commerce:commerce@localhost:10432/commerce?sslmode=disable"


def _connect(url: str):
    return psycopg2.connect(url)


def _load_rows(path: Path) -> tuple[list[str], list[tuple[object, ...]]]:
    with path.open(encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        return header, [tuple(row) for row in reader]


def _upsert(cursor, table: str, header: list[str], rows: list[tuple[object, ...]], key: str) -> int:
    columns = ", ".join(header)
    placeholders = ", ".join(["%s"] * len(header))
    updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in header if column != key)
    statement = (
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT ({key}) DO UPDATE SET {updates}"
    )
    cursor.executemany(statement, rows)
    return len(rows)


def seed_base(url: str, data_dir: Path) -> dict[str, int]:
    """Create source tables and load the base fixture state."""
    counts: dict[str, int] = {}
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS customers (
                    customer_id varchar PRIMARY KEY,
                    email varchar NOT NULL,
                    full_name varchar NOT NULL,
                    segment varchar NOT NULL,
                    region varchar NOT NULL,
                    signup_date date NOT NULL,
                    updated_at timestamptz NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    order_id varchar PRIMARY KEY,
                    email varchar NOT NULL,
                    status varchar NOT NULL,
                    currency varchar NOT NULL,
                    total_amount numeric(12, 2) NOT NULL,
                    ordered_at timestamptz NOT NULL,
                    updated_at timestamptz NOT NULL
                )
                """
            )
            connection.commit()
            for table, key in (("customers", "customer_id"), ("orders", "order_id")):
                path = data_dir / "commerce" / "base" / f"{table}.csv"
                if not path.exists():
                    continue
                header, rows = _load_rows(path)
                counts[table] = _upsert(cursor, table, header, rows, key)
        connection.commit()
    return counts


def seed_update(url: str, data_dir: Path) -> dict[str, int]:
    """Apply the watermark-newer delta set."""
    counts: dict[str, int] = {}
    with _connect(url) as connection:
        with connection.cursor() as cursor:
            for table, key in (("customers", "customer_id"), ("orders", "order_id")):
                path = data_dir / "commerce" / "update" / f"{table}.csv"
                if not path.exists():
                    continue
                header, rows = _load_rows(path)
                counts[table] = _upsert(cursor, table, header, rows, key)
        connection.commit()
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--update", action="store_true", help="apply the delta set instead of base")
    args = parser.parse_args()
    applied = (
        seed_update(args.url, args.data_dir) if args.update else seed_base(args.url, args.data_dir)
    )
    print(json.dumps(applied, sort_keys=True))
