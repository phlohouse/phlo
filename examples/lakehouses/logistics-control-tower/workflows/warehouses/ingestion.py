"""DLT ingestion of warehouse scan CSVs.

Every warehouse drops one ``scans.csv`` file; all files are merged into a single
``warehouse_scans`` table keyed by ``scan_id``. Re-reading a day is idempotent
because scans merge on their key.
"""

from __future__ import annotations

from pathlib import Path

import dlt
import pandas as pd
import phlo
from phlo.contracts import SLA, Consumer

from workflows.schemas.logistics import WarehouseScanSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WAREHOUSE_DIR = PROJECT_ROOT / "generated-data" / "warehouses"


def read_warehouse_scans(
    warehouse_dir: Path = WAREHOUSE_DIR, partition_date: str = ""
) -> pd.DataFrame:
    """Read every warehouse CSV, optionally restricted to one scanned-on date."""
    frames: list[pd.DataFrame] = []
    for path in sorted(warehouse_dir.glob("*/scans.csv")):
        frame = pd.read_csv(path, dtype=str)
        if partition_date and not frame["scanned_at"].str.startswith(partition_date).any():
            continue
        frames.append(frame)
    if not frames:
        raise FileNotFoundError(f"No warehouse scans found under {warehouse_dir}")
    return pd.concat(frames, ignore_index=True)


@phlo.ingest.dlt(
    table_name="warehouse_scans",
    unique_key="scan_id",
    validation_schema=WarehouseScanSchema,
    group="warehouses",
    freshness_hours=(8, 12),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=300,
    max_retries=3,
    retry_delay_seconds=60,
    add_metadata_columns=True,
    owner="logistics-warehouse-ops",
    consumers=[
        Consumer(name="control-tower", usage="physical handling milestones"),
        Consumer(name="site-managers", usage="dwell-time review"),
    ],
    sla=SLA(freshness_hours=12, quality_threshold=0.99),
)
def warehouse_scans(partition_date: str) -> object:
    """Merge every warehouse's scan file; replays deduplicate on scan_id."""
    return dlt.resource(
        read_warehouse_scans(partition_date=partition_date).to_dict("records"),
        name="warehouse_scans",
    )
