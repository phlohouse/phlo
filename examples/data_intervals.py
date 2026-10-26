"""Run a bounded order-ingestion lakehouse on configured Nessie/MinIO services.

From the repository root: uv run --locked python examples/data_intervals.py
Uses a temporary SQLite source and a unique Iceberg table on the configured ref.
The table is retained for inspection; the source database is temporary.
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import dagster as dg
import dlt
from phlo_dagster.adapter import DagsterOrchestratorAdapter
from phlo_iceberg.resource import IcebergResource
from pyiceberg.schema import Schema
from pyiceberg.types import LongType, NestedField, TimestamptzType

from phlo import ingest
from phlo.capabilities import DataInterval, TableStoreSpec, register_capability


def main() -> None:
    """Materialise adjacent London days, a late arrival, then an exact replay."""
    table_name = f"interval_orders_{uuid4().hex[:8]}"
    store = IcebergResource()
    register_capability("table_store", TableStoreSpec(name="iceberg", provider=store))
    schema = Schema(
        NestedField(1, "id", LongType(), required=False),
        NestedField(2, "updated_at", TimestamptzType(), required=False),
    )
    with TemporaryDirectory(prefix="phlo-interval-source-") as directory:
        db = sqlite3.connect(Path(directory) / "orders.db")
        db.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, updated_at TEXT)")
        db.executemany(
            "INSERT INTO orders VALUES (?, ?)",
            [
                (101, "2026-03-29T00:00:00+00:00"),
                (202, "2026-03-29T23:00:00+00:00"),
                (303, "2026-03-30T23:00:00+00:00"),
            ],
        )
        db.commit()

        @ingest.dlt(
            table_name=table_name,
            unique_key="id",
            group="raw",
            namespace="raw",
            table_schema=schema,
            validate=False,
            add_metadata_columns=False,
            schema_policy="drop_extra",
            interval_column="updated_at",
            interval_lookback=timedelta(hours=2),
            capabilities={"table_store": "iceberg"},
        )
        def orders(data_interval: DataInterval):
            # Push both framework bounds into the database query. DLT applies
            # the same half-open guard before staging without changing cursors.
            @dlt.resource(name="orders", primary_key="id")
            def bounded_orders():
                selected = db.execute(
                    "SELECT id, updated_at FROM orders WHERE updated_at >= ? AND updated_at < ? ORDER BY id",
                    (data_interval.start.isoformat(), data_interval.end.isoformat()),
                ).fetchall()
                print(
                    f"EXTRACT [{data_interval.start.isoformat()}, {data_interval.end.isoformat()}) "
                    f"ids={[row[0] for row in selected]}",
                    flush=True,
                )
                for identifier, timestamp in selected:
                    yield {"id": identifier, "updated_at": datetime.fromisoformat(timestamp)}

            return bounded_orders()

        specs = [spec for spec in ingest.assets("dlt") if spec.key == f"dlt_{table_name}"]
        definitions = DagsterOrchestratorAdapter().build_definitions(
            assets=specs, checks=[], resources=[]
        )
        asset = definitions.get_assets_def(specs[0].key)
        for day, mode in [
            ("2026-03-29", "scheduled"),
            ("2026-03-30", "scheduled"),
            ("2026-03-29", "replay"),
        ]:
            if day == "2026-03-30":
                db.execute("INSERT INTO orders VALUES (404, '2026-03-29T22:30:00+00:00')")
                db.commit()
            result = dg.materialize(
                [asset],
                partition_key=day,
                tags={"phlo/interval_mode": mode, "phlo/project": "interval-demo"},
            )
            assert result.success
            rows = store.get_catalog().load_table(f"raw.{table_name}").scan().to_arrow().to_pylist()
            identifiers = sorted(row["id"] for row in rows)
            expected = [101] if day == "2026-03-29" and mode == "scheduled" else [101, 202, 404]
            assert identifiers == expected, (identifiers, expected)
            print(f"DESTINATION {day} {mode} ids={identifiers}", flush=True)
        db.close()
    print(
        f"INSPECT raw.{table_name}: boundary 202 retained; late 404 recovered; no duplicate ids",
        flush=True,
    )


if __name__ == "__main__":
    main()
