"""DLT ingestion of the finance-domain invoice stream.

Invoices are immutable once issued, so the raw asset appends. It is the only
partitioned asset in this example: daily identity partitions on ``issued_on``
match how the billing system emits closed batches.
"""

from __future__ import annotations

import json
from pathlib import Path

import dlt
import pandas as pd
import phlo
from phlo.contracts import SLA, Consumer

from workflows.finance.quality import check_amounts_positive, make_known_deals_check
from workflows.finance.schemas import InvoiceSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FINANCE_DIR = PROJECT_ROOT / "generated-data" / "finance"


def read_invoices(data_dir: Path = FINANCE_DIR, partition_date: str = "") -> pd.DataFrame:
    """Read invoices, optionally restricted to one issue day."""
    path = data_dir / "invoices.json"
    if not path.exists():
        raise FileNotFoundError(f"Invoice stream missing: {path}")
    records = json.loads(path.read_text(encoding="utf-8"))
    frame = pd.DataFrame(records)
    if partition_date:
        issued = pd.to_datetime(frame["issued_on"], errors="coerce")
        frame = frame[issued.dt.strftime("%Y-%m-%d") == partition_date]
    if frame.empty:
        raise FileNotFoundError(
            f"No invoices found for partition '{partition_date or '*'}' under {data_dir}"
        )
    return frame.reset_index(drop=True)


@phlo.ingest.dlt(
    table_name="finance_invoices",
    unique_key="invoice_id",
    validation_schema=InvoiceSchema,
    group="finance_billing",
    freshness_hours=(48, 96),
    merge_strategy="append",
    partition_spec=[("issued_on", "day")],
    strict_validation=True,
    max_runtime_seconds=300,
    max_retries=3,
    retry_delay_seconds=120,
    add_metadata_columns=True,
    owner="billing-ops",
    consumers=[Consumer(name="finance", usage="invoice aging and collections")],
    sla=SLA(freshness_hours=96, quality_threshold=1.0),
    quality_checks=[check_amounts_positive, make_known_deals_check()],
)
def finance_invoices(partition_date: str) -> object:
    """Append one issue day of invoices; attribution is validated against sales."""
    return dlt.resource(
        read_invoices(partition_date=partition_date).to_dict("records"),
        name="finance_invoices",
    )
