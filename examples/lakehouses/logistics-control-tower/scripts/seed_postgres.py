"""Seed the local logistics PostgreSQL source from generated fixtures.

Usage:
    python scripts/seed_postgres.py --stage base
    python scripts/seed_postgres.py --stage update

``base`` replays every version row from ``generated-data/base/orders.csv`` in
order, so the source database ends on its latest state. ``update`` upserts the
strictly-newer delta from ``generated-data/update/orders.csv``; a subsequent
incremental Sling run picks up exactly those rows via the ``updated_at``
watermark.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "generated-data"
DEFAULT_URL = "postgresql://logistics:logistics@localhost:10332/logistics"

CREATE_ORDERS = """
create table if not exists public.orders (
    order_id text primary key,
    customer_ref text not null,
    status text not null,
    ordered_at timestamptz not null,
    updated_at timestamptz not null
)
"""


UPSERT_ORDER = """
insert into public.orders
    (order_id, customer_ref, status, ordered_at, updated_at)
values (%(order_id)s, %(customer_ref)s, %(status)s, %(ordered_at)s, %(updated_at)s)
on conflict (order_id) do update set
    customer_ref = excluded.customer_ref,
    status = excluded.status,
    ordered_at = excluded.ordered_at,
    updated_at = excluded.updated_at
"""


def _connect(url: str):
    return psycopg2.connect(url)


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def seed_base(url: str, data_dir: Path) -> int:
    """Create the orders table and replay every base version in order."""
    rows = _load_rows(data_dir / "base" / "orders.csv")
    with _connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_ORDERS)
            for row in rows:
                cur.execute(UPSERT_ORDER, row)
    return len(rows)


def seed_update(url: str, data_dir: Path) -> int:
    """Upsert the delta rows; each is strictly newer than the watermark."""
    rows = _load_rows(data_dir / "update" / "orders.csv")
    with _connect(url) as conn:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(UPSERT_ORDER, row)
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="source DSN")
    parser.add_argument("--stage", choices=("base", "update"), required=True)
    args = parser.parse_args()
    if args.stage == "base":
        applied = seed_base(args.url, DATA_DIR)
        print(f"replayed {applied} order versions into {args.url.split('@')[-1]}")
    else:
        applied = seed_update(args.url, DATA_DIR)
        print(f"applied {applied} delta rows into {args.url.split('@')[-1]}")
