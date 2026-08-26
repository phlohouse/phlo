"""DLT ingestion of the paginated civic place registry from the replay API.

The registry is the geographic backbone of the example: every weather
station id must resolve to a place, and research models join regions and
demographics through it. Rows merge on ``place_id``, so a later page that
restates a place (upstream revision) updates exactly the changed fields -
and Nessie time travel recovers the pre-revision table state.
"""

from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

import dlt
import pandas as pd
import phlo
from phlo.contracts import SLA, Consumer

from workflows.schemas.contracts import PlaceRecordSchema

API_URL = os.environ.get("CIVIC_API_URL", "http://127.0.0.1:8094/v1")


def fetch_places(
    registry_date: str, url: str = API_URL, max_attempts: int = 3
) -> list[dict[str, object]]:
    """Fetch every page of one registry date from the civic API."""
    rows: list[dict[str, object]] = []
    cursor = 0
    while True:
        params = urlencode({"registry_date": registry_date, "cursor": cursor})
        payload = _request_with_retry(f"{url}/places?{params}", max_attempts)
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
    table_name="places_registry",
    unique_key="place_id",
    validation_schema=PlaceRecordSchema,
    group="civic_api",
    freshness_hours=(26, 30),
    merge_strategy="merge",
    partition_spec=[("registry_date", "identity")],
    strict_validation=True,
    max_runtime_seconds=300,
    max_retries=3,
    retry_delay_seconds=60,
    add_metadata_columns=True,
    owner="civic-platform",
    consumers=[
        Consumer(name="research", usage="geographic spine for places and indicators"),
        Consumer(name="operations", usage="station coverage validation"),
    ],
    sla=SLA(freshness_hours=30, quality_threshold=1.0),
)
def places_registry(partition_date: str) -> object:
    """Merge one day's registry pages; upstream revisions update in place."""
    frame = pd.DataFrame(fetch_places(registry_date=partition_date))
    frame["registry_date"] = f"{partition_date}T00:00:00Z"
    return dlt.resource(frame.to_dict("records"), name="places_registry")
