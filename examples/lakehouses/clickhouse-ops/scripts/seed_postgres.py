"""Seed the local tenant-metadata PostgreSQL source from generated fixtures.

Run after ``docker compose up -d chmeta-postgres`` (host port 10832) and before
materializing the accounts stream:

    uv run python scripts/generate_fixtures.py
    uv run python scripts/seed_postgres.py
    uv run phlo materialize sling_chmeta_tenants
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "generated-data"
DEFAULT_URL = "postgresql://chmeta:chmeta@localhost:10832/chmeta"

COLUMNS = ["tenant_id", "tenant_name", "tier", "plan"]
PRIMARY_KEY = "tenant_id"


def _connect(url: str):
    return psycopg2.connect(url)


def seed_base(url: str, data_dir: Path = DATA_DIR) -> int:
    """Create public.tenants and load the fixture CSV; returns rows loaded."""
    path = data_dir / "accounts" / "tenants.csv"
    with path.open(encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        rows = [tuple(row) for row in reader]
    if header != COLUMNS:
        raise ValueError(f"Unexpected tenants.csv header: {header}")
    with _connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS public.tenants ("
                f"{', '.join(f'{col} TEXT' for col in COLUMNS)}, "
                f"PRIMARY KEY ({PRIMARY_KEY}))"
            )
            for row in rows:
                placeholders = ", ".join(["%s"] * len(COLUMNS))
                updates = ", ".join(f"{col} = EXCLUDED.{col}" for col in COLUMNS[1:])
                cur.execute(
                    f"INSERT INTO public.tenants ({', '.join(COLUMNS)}) "
                    f"VALUES ({placeholders}) "
                    f"ON CONFLICT ({PRIMARY_KEY}) DO UPDATE SET {updates}",
                    row,
                )
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="PostgreSQL DSN of the metadata source")
    args = parser.parse_args()
    loaded = seed_base(args.url)
    print(f"seeded {loaded} tenants into {args.url.split('@')[-1]}")
