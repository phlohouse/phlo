"""DLT ingestion of the pipe-delimited eligibility file."""

from __future__ import annotations

import csv
from pathlib import Path

import dlt
import phlo
from phlo.contracts import SLA, Consumer

from workflows.shared.contracts.schemas import EligibilityPeriodSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ELIGIBILITY_FILE = PROJECT_ROOT / "generated-data" / "inbound" / "eligibility" / "eligibility.csv"


def read_eligibility(eligibility_file: Path = ELIGIBILITY_FILE) -> list[dict[str, str]]:
    """Parse pipe-delimited coverage periods."""
    with eligibility_file.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="|"))


@phlo.ingest.dlt(
    table_name="eligibility_periods",
    unique_key="eligibility_key",
    validation_schema=EligibilityPeriodSchema,
    group="eligibility",
    partitioned=False,
    freshness_hours=(50, 54),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=1800,
    max_retries=1,
    retry_delay_seconds=120,
    add_metadata_columns=True,
    owner="enrollment-operations",
    consumers=[
        Consumer(name="compliance-officer", usage="coverage verification"),
        Consumer(name="claims-operations", usage="temporal validity joins"),
    ],
    sla=SLA(freshness_hours=54, quality_threshold=1.0),
)
def eligibility_periods(partition_date: str) -> object:
    """Merge coverage periods; open-ended coverage uses a far-future end."""
    del partition_date
    return dlt.resource(read_eligibility(), name="eligibility_periods")
