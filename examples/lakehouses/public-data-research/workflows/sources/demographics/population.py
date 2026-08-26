"""DLT ingestion of annual regional demographic files.

Each ``demographics-<year>.csv`` carries one row per region at annual grain.
Runs are daily partitions that resolve to their calendar year; rows merge on
the ``(region, year)`` surrogate key ``region_year``, so a revised census
file replaces its year in place. The derived ``census_year`` date key drives
identity (annual) partitioning in the table store.
"""

from __future__ import annotations

from pathlib import Path

import dlt
import pandas as pd
import phlo
from phlo.contracts import SLA, Consumer

from workflows.schemas.contracts import RegionDemographicsSchema

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEMOGRAPHICS_DIR = PROJECT_ROOT / "generated-data" / "demographics"


def read_demographics_year(
    year_label: str, demographics_dir: Path = DEMOGRAPHICS_DIR
) -> pd.DataFrame:
    """Read one annual file and derive the surrogate and partition keys."""
    path = demographics_dir / f"demographics-{year_label}.csv"
    if not path.exists():
        raise FileNotFoundError(f"No demographics file for '{year_label}' under {demographics_dir}")
    frame = pd.read_csv(path, dtype={"region": str})
    frame["year"] = frame["year"].astype(int)
    frame["region_year"] = frame["region"] + "|" + frame["year"].astype(str)
    frame["census_year"] = pd.Timestamp(f"{year_label}-01-01")
    return frame


@phlo.ingest.dlt(
    table_name="region_demographics",
    unique_key="region_year",
    validation_schema=RegionDemographicsSchema,
    group="demographics",
    freshness_hours=(8760, 8800),
    merge_strategy="merge",
    partition_spec=[("census_year", "identity")],
    strict_validation=True,
    max_runtime_seconds=120,
    max_retries=1,
    retry_delay_seconds=60,
    add_metadata_columns=True,
    owner="demographics-ops",
    consumers=[Consumer(name="research", usage="denominators for regional rollups")],
    sla=SLA(freshness_hours=8800, quality_threshold=1.0),
)
def region_demographics(partition_date: str) -> object:
    """Merge one census year of regional statistics on the surrogate key."""
    return dlt.resource(
        read_demographics_year(partition_date[:4]).to_dict("records"),
        name="region_demographics",
    )
