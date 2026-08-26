"""DLT ingestion of the sales-domain CRM deal extract.

The CRM delivers one full snapshot per refresh with no change feed, so the
raw asset is a reference-style merge on ``deal_id`` with partitions disabled.
"""

from __future__ import annotations

from pathlib import Path

import dlt
import pandas as pd
import phlo
from phlo.contracts import SLA, Consumer

from workflows.sales.quality import check_deal_ids_unique, check_stage_vocabulary
from workflows.sales.schemas import DealSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SALES_DIR = PROJECT_ROOT / "generated-data" / "sales"


def read_deals(data_dir: Path = SALES_DIR) -> pd.DataFrame:
    """Read the CRM deal extract snapshot."""
    path = data_dir / "deals.csv"
    if not path.exists():
        raise FileNotFoundError(f"Sales deal extract missing: {path}")
    return pd.read_csv(path)


@phlo.ingest.dlt(
    table_name="sales_deals",
    unique_key="deal_id",
    validation_schema=DealSchema,
    group="sales_crm",
    partitioned=False,
    freshness_hours=(168, 336),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=300,
    max_retries=2,
    retry_delay_seconds=300,
    add_metadata_columns=True,
    owner="revenue-ops",
    consumers=[
        Consumer(name="finance", usage="invoice-to-deal attribution"),
        Consumer(name="revenue-ops", usage="pipeline reporting"),
    ],
    sla=SLA(freshness_hours=336, quality_threshold=1.0),
    quality_checks=[check_stage_vocabulary, check_deal_ids_unique],
)
def sales_deals(partition_date: str) -> object:
    """Merge the current CRM snapshot; replays are idempotent."""
    del partition_date
    return dlt.resource(read_deals().to_dict("records"), name="sales_deals")
