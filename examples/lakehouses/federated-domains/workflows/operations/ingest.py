"""DLT ingestion of the operations-domain incident stream.

The incident tracker upserts records as they reopen and resolve, so the raw
asset merges on ``incident_id`` with partitions disabled. Incidents change
fast, so freshness windows are the tightest of the three domains.
"""

from __future__ import annotations

from pathlib import Path

import dlt
import pandas as pd
import phlo
from phlo.contracts import SLA, Consumer

from workflows.operations.quality import (
    check_resolution_consistency,
    check_severity_vocabulary,
)
from workflows.operations.schemas import IncidentSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OPERATIONS_DIR = PROJECT_ROOT / "generated-data" / "operations"


def read_incidents(data_dir: Path = OPERATIONS_DIR) -> pd.DataFrame:
    """Read the incident extract."""
    path = data_dir / "incidents.csv"
    if not path.exists():
        raise FileNotFoundError(f"Incident extract missing: {path}")
    return pd.read_csv(path)


@phlo.ingest.dlt(
    table_name="operations_incidents",
    unique_key="incident_id",
    validation_schema=IncidentSchema,
    group="operations_reliability",
    partitioned=False,
    freshness_hours=(24, 48),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=300,
    max_retries=3,
    retry_delay_seconds=120,
    add_metadata_columns=True,
    owner="sre",
    consumers=[Consumer(name="sre", usage="service reliability reporting")],
    sla=SLA(freshness_hours=48, quality_threshold=0.99, notify=["sre"]),
    quality_checks=[check_severity_vocabulary, check_resolution_consistency],
)
def operations_incidents(partition_date: str) -> object:
    """Merge the current incident state; reopened rows overwrite in place."""
    del partition_date
    return dlt.resource(read_incidents().to_dict("records"), name="operations_incidents")
