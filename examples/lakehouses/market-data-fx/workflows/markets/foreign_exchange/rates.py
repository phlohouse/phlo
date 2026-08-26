"""DLT ingestion of daily FX rate observations from the replay API."""

from __future__ import annotations

import json
import os
from urllib.parse import urlencode
from urllib.request import urlopen

import dlt
import phlo
from phlo.contracts import SLA, Consumer

from workflows.markets.foreign_exchange.quality import assert_fx_cross_tolerance
from workflows.schemas.market_data import FxRateSchema

API_URL = os.environ.get("MARKETS_API_URL", "http://127.0.0.1:8092/v1")


def fetch_rates(rate_date: str, url: str = API_URL) -> list[dict[str, object]]:
    """Fetch one trade date's FX observations."""
    request_url = f"{url}/fx?{urlencode({'rate_date': rate_date})}"
    with urlopen(request_url, timeout=10) as response:  # noqa: S310 - replay endpoint
        payload = json.load(response)
    return payload["data"]


@phlo.ingest.dlt(
    table_name="fx_rates",
    unique_key="rate_id",
    validation_schema=FxRateSchema,
    group="foreign_exchange",
    quality_checks=[assert_fx_cross_tolerance],
    freshness_hours=(26, 30),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=300,
    max_retries=2,
    retry_delay_seconds=60,
    add_metadata_columns=True,
    owner="treasury-ops",
    consumers=[Consumer(name="research", usage="currency normalization")],
    sla=SLA(freshness_hours=30, quality_threshold=1.0),
)
def fx_rates(partition_date: str) -> object:
    """Merge one trade date's rates; corrected observations replace in place."""
    return dlt.resource(fetch_rates(rate_date=partition_date), name="fx_rates")
