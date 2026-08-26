"""Inspect the history and maintenance state of the Delta tables.

Delta Lake exposes version history through the provider itself, so this is a
supported diagnostic (unlike Iceberg siblings, no Trino round-trip involved):

    uv run python scripts/delta_history.py [table ...]

For each table it prints recent versions from ``DeltaResource.list_snapshots``
plus maintenance recommendations derived from the physical file layout. Point
``DELTA_WAREHOUSE_PATH`` at a local directory to inspect a checkout-scale
warehouse without MinIO.
"""

from __future__ import annotations

import argparse

from phlo_delta.resource import DeltaResource

DEFAULT_TABLES = ["raw.telemetry_readings", "raw.telemetry_corrections", "raw.delta_regions"]


def describe_table(table_store: DeltaResource, table_name: str, limit: int) -> dict[str, object]:
    """Return version history plus maintenance recommendations for one table."""
    versions = table_store.list_snapshots(table_name=table_name, limit=limit)
    try:
        from phlo_delta.helpers import recommend_table_maintenance

        maintenance: list[str] | str = recommend_table_maintenance(table_name)
    except AttributeError as exc:
        # Platform gap: phlo_delta's stats helpers call DeltaTable.files(),
        # which deltalake >= 1 removed. History stays available; maintenance
        # recommendations need a compatible deltalake pin upstream.
        maintenance = f"unavailable on this deltalake version ({exc})"
    return {
        "table": table_name,
        "current_version": table_store.get_table(table_name).version(),
        "versions": [
            {
                "version": version["version"],
                "operation": version.get("operation"),
                "timestamp": version.get("timestamp"),
            }
            for version in versions
        ],
        "maintenance": maintenance,
    }


def main(tables: list[str], limit: int = 10) -> list[dict[str, object]]:
    """Print a compact history report for every requested table."""
    table_store = DeltaResource()
    reports = [describe_table(table_store, table, limit) for table in tables]
    for report in reports:
        print(f"== {report['table']} (version {report['current_version']})")
        for version in report["versions"]:
            print(f"   v{version['version']}: {version['operation']} @ {version['timestamp']}")
        print(f"   maintenance: {report['maintenance']}")
    return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tables", nargs="*", default=DEFAULT_TABLES)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    main(args.tables or DEFAULT_TABLES, args.limit)
