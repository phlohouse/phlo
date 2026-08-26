"""Seed the compose PostgreSQL source with the regions lookup.

Run after ``docker compose up -d`` from this directory:

    uv run python scripts/seed_postgres.py

Reads the deterministic ``regions.csv`` replay fixture and idempotently
upserts it into ``public.regions`` so the Sling full-refresh always sees the
same rows.
"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT / "generated-data" / "regions" / "regions.csv"
DEFAULT_DSN = "postgresql://delta:delta@localhost:10732/delta?sslmode=disable"

DDL = """
CREATE TABLE IF NOT EXISTS public.regions (
    region_code TEXT PRIMARY KEY,
    region_name TEXT NOT NULL,
    country TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
)
"""

UPSERT = """
INSERT INTO public.regions (region_code, region_name, country, updated_at)
VALUES (%s, %s, %s, %s)
ON CONFLICT (region_code) DO UPDATE
SET region_name = EXCLUDED.region_name,
    country = EXCLUDED.country,
    updated_at = EXCLUDED.updated_at
"""


def main(csv_path: Path = DEFAULT_CSV, dsn: str | None = None) -> int:
    import psycopg2

    rows = list(csv.reader(csv_path.open(encoding="utf-8")))
    records = rows[1:]
    connection = psycopg2.connect(dsn or os.environ.get("REGIONS_SOURCE_URL", DEFAULT_DSN))
    try:
        with connection.cursor() as cursor:
            cursor.execute(DDL)
            for row in records:
                cursor.execute(UPSERT, row)
        connection.commit()
    finally:
        connection.close()
    return len(records)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--dsn", default=None)
    args = parser.parse_args()
    print(f"Upserted {main(args.csv, args.dsn)} regions into public.regions")
