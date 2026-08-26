"""DLT ingestion of daily claim arrival files."""

from __future__ import annotations

import csv
from pathlib import Path

import dlt
import phlo
from phlo.contracts import SLA, Consumer

from workflows.claims.quality import assert_versions_unique_and_advancing
from workflows.shared.contracts.schemas import ClaimSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INBOUND_DIR = PROJECT_ROOT / "generated-data" / "inbound" / "claims"


def read_arrival(arrival_date: str, inbound_dir: Path = INBOUND_DIR) -> list[dict[str, str]]:
    """Read one day's claim file; the partition is the arrival date."""
    path = inbound_dir / f"claims-{arrival_date}.csv"
    if not path.exists():
        raise FileNotFoundError(f"No claim arrival file for {arrival_date}: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@phlo.ingest.dlt(
    table_name="claims",
    unique_key="claim_version_key",
    validation_schema=ClaimSchema,
    group="claims",
    quality_checks=[assert_versions_unique_and_advancing],
    freshness_hours=(26, 30),
    merge_strategy="append",
    strict_validation=True,
    max_runtime_seconds=1800,
    max_retries=1,
    retry_delay_seconds=120,
    add_metadata_columns=True,
    owner="claims-operations",
    consumers=[
        Consumer(name="compliance-officer", usage="regulatory reporting"),
        Consumer(name="actuarial", usage="utilization and cost trends"),
    ],
    sla=SLA(freshness_hours=30, quality_threshold=1.0, notify=["claims-operations"]),
)
def claims(partition_date: str) -> object:
    """Append one day's arrivals; re-filed versions accumulate for audit."""
    return dlt.resource(
        read_arrival(partition_date),
        name="claims",
    )
