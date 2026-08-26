"""DLT ingestion of paginated vendor bars and late print corrections."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

import dlt
import phlo
from phlo.contracts import SLA, Consumer

from workflows.schemas.market_data import EquitiesBarSchema, EquityCorrectionSchema

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CORRECTIONS_FILE = PROJECT_ROOT / "generated-data" / "api" / "corrections.json"
API_URL = os.environ.get("MARKETS_API_URL", "http://127.0.0.1:8092/v1")


def read_corrections(corrections_file: Path = CORRECTIONS_FILE) -> list[dict[str, object]]:
    """Load the correction batch merged onto previously ingested bars."""
    return json.loads(corrections_file.read_text(encoding="utf-8"))


def fetch_bars(
    trade_date: str, url: str = API_URL, max_attempts: int = 3
) -> list[dict[str, object]]:
    """Fetch every page of one trade date's bars from the vendor API."""
    rows: list[dict[str, object]] = []
    cursor = 0
    while True:
        request_url = f"{url}/bars?{urlencode({'trade_date': trade_date, 'cursor': cursor})}"
        payload = _request_with_retry(request_url, max_attempts)
        rows.extend(payload["data"])
        next_cursor = payload["next_cursor"]
        if next_cursor is None:
            return rows
        cursor = int(next_cursor)


def _request_with_retry(request_url: str, max_attempts: int) -> dict[str, object]:
    for attempt in range(max_attempts):
        try:
            with urlopen(request_url, timeout=10) as response:  # noqa: S310 - replay endpoint
                return json.load(response)
        except HTTPError as error:
            if error.code < 500 or attempt == max_attempts - 1:
                raise
            time.sleep(float(error.headers.get("Retry-After", "1")))
    raise RuntimeError("unreachable")  # pragma: no cover - loop always returns or raises


@phlo.ingest.dlt(
    table_name="equities_bars",
    unique_key="bar_id",
    validation_schema=EquitiesBarSchema,
    group="prices",
    freshness_hours=(16, 20),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=600,
    max_retries=3,
    retry_delay_seconds=60,
    add_metadata_columns=True,
    owner="markets-data-eng",
    consumers=[
        Consumer(name="research", usage="return and risk analytics"),
        Consumer(name="treasury", usage="currency exposure reporting"),
    ],
    sla=SLA(freshness_hours=20, quality_threshold=0.995, notify=["markets-data-eng"]),
)
def equities_bars(partition_date: str) -> object:
    """Merge one trade date's bars; replays are idempotent by bar id."""
    return dlt.resource(
        fetch_bars(trade_date=partition_date),
        name="equities_bars",
    )


@phlo.ingest.dlt(
    table_name="equity_corrections",
    unique_key="bar_id",
    validation_schema=EquityCorrectionSchema,
    group="prices",
    freshness_hours=(48, 54),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=300,
    max_retries=2,
    retry_delay_seconds=60,
    add_metadata_columns=True,
    owner="markets-data-eng",
    consumers=[Consumer(name="research", usage="corrected historical analytics")],
    sla=SLA(freshness_hours=54, quality_threshold=1.0),
)
def equity_corrections(partition_date: str) -> object:
    """Merge the correction batch onto previously ingested bar ids."""
    del partition_date
    return dlt.resource(read_corrections(), name="equity_corrections")
