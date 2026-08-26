"""DLT ingestion of security master, trading calendar, and portfolio holdings."""

from __future__ import annotations

from pathlib import Path

import dlt
import pandas as pd
import phlo
from phlo.contracts import SLA, Consumer

from workflows.schemas.market_data import (
    CalendarEntrySchema,
    PortfolioHoldingSchema,
    SecurityMasterSchema,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_DIR = PROJECT_ROOT / "generated-data" / "reference"


def read_security_master(reference_dir: Path = REFERENCE_DIR) -> pd.DataFrame:
    return pd.read_csv(reference_dir / "security_master.csv", dtype=str)


def read_trading_calendar(reference_dir: Path = REFERENCE_DIR) -> pd.DataFrame:
    calendar = pd.read_csv(reference_dir / "trading_calendar.csv", dtype=str)
    calendar["is_trading_day"] = calendar["is_trading_day"].str.lower() == "true"
    calendar["calendar_key"] = calendar["market"] + "|" + calendar["calendar_date"]
    return calendar


def read_holdings(reference_dir: Path = REFERENCE_DIR) -> pd.DataFrame:
    holdings = pd.read_csv(reference_dir / "portfolio_holdings.csv", dtype=str)
    holdings["quantity"] = holdings["quantity"].astype(int)
    holdings["holding_key"] = holdings["portfolio"] + "|" + holdings["symbol"]
    return holdings


@phlo.ingest.dlt(
    table_name="security_master",
    unique_key="symbol",
    validation_schema=SecurityMasterSchema,
    group="reference_data",
    partitioned=False,
    freshness_hours=(168, 192),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=120,
    max_retries=1,
    retry_delay_seconds=60,
    add_metadata_columns=True,
    owner="reference-data",
    consumers=[Consumer(name="research", usage="instrument metadata")],
    sla=SLA(freshness_hours=192, quality_threshold=1.0),
)
def security_master(partition_date: str) -> object:
    """Merge the instrument master; currency and timezone drive normalization."""
    del partition_date
    return dlt.resource(
        read_security_master().to_dict("records"),
        name="security_master",
    )


@phlo.ingest.dlt(
    table_name="trading_calendar",
    unique_key="calendar_key",
    validation_schema=CalendarEntrySchema,
    group="reference_data",
    partitioned=False,
    freshness_hours=(168, 192),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=120,
    max_retries=1,
    retry_delay_seconds=60,
    add_metadata_columns=True,
    owner="reference-data",
    consumers=[
        Consumer(name="research", usage="calendar-aware session sequencing"),
        Consumer(name="operations", usage="expected-observation coverage"),
    ],
    sla=SLA(freshness_hours=192, quality_threshold=1.0),
)
def trading_calendar(partition_date: str) -> object:
    """Merge per-market trading-day flags; closures are explicit, not gaps."""
    del partition_date
    return dlt.resource(read_trading_calendar().to_dict("records"), name="trading_calendar")


@phlo.ingest.dlt(
    table_name="portfolio_holdings",
    unique_key="holding_key",
    validation_schema=PortfolioHoldingSchema,
    group="reference_data",
    partitioned=False,
    freshness_hours=(720, 744),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=60,
    max_retries=1,
    retry_delay_seconds=60,
    add_metadata_columns=True,
    owner="portfolio-ops",
    consumers=[Consumer(name="treasury", usage="exposure analytics")],
    sla=SLA(freshness_hours=744, quality_threshold=1.0),
)
def portfolio_holdings(partition_date: str) -> object:
    """Merge static portfolio positions."""
    del partition_date
    return dlt.resource(read_holdings().to_dict("records"), name="portfolio_holdings")
