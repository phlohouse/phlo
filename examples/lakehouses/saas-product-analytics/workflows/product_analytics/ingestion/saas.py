"""DLT assets for paginated SaaS events and account-plan CSV snapshots."""

from __future__ import annotations

import csv
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

from workflows.product_analytics.schemas.events import AccountPlansSchema, EventsSchema

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "generated-data"
API_URL = os.getenv("SAAS_REPLAY_URL", "http://127.0.0.1:8091/v1/events")


def normalize_event(raw_event: dict[str, object]) -> dict[str, object]:
    """Flatten the API envelope while retaining every analytics field we consume."""
    account = raw_event["account"]
    actor = raw_event["actor"]
    event = raw_event["event"]
    context = raw_event["context"]
    properties = event.get("properties", {})
    return {
        "event_id": raw_event["event_id"],
        "occurred_at": raw_event["occurred_at"],
        "account_id": account["id"],
        "account_name": account["name"],
        "actor_id": actor["id"],
        "actor_email": actor["email"],
        "event_type": event["type"],
        "feature": properties.get("feature"),
        "experiment_variant": properties.get("experiment_variant"),
        "session_id": context["session_id"],
        "release": context["release"],
    }


def read_paginated_events(url: str = API_URL, max_attempts: int = 3) -> list[dict[str, object]]:
    cursor: str | None = "0"
    events: list[dict[str, object]] = []
    while cursor is not None:
        request_url = f"{url}?{urlencode({'cursor': cursor})}"
        for attempt in range(max_attempts):
            try:
                with urlopen(request_url, timeout=10) as response:  # noqa: S310 - local replay endpoint
                    payload = json.load(response)
                break
            except HTTPError as error:
                if error.code != 429 or attempt == max_attempts - 1:
                    raise
                time.sleep(float(error.headers.get("Retry-After", "0")))
        else:  # pragma: no cover - retry loop always returns or raises
            raise RuntimeError("unreachable")
        events.extend(normalize_event(raw_event) for raw_event in payload["data"])
        cursor = payload["next_cursor"]
    return events


@phlo.ingest.dlt(
    table_name="saas_events",
    unique_key="event_id",
    validation_schema=EventsSchema,
    group="product_analytics",
    freshness_hours=(1, 2),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=300,
    max_retries=3,
    retry_delay_seconds=1,
    add_metadata_columns=True,
    owner="product-analytics",
    consumers=[Consumer(name="product", usage="activation and retention")],
    sla=SLA(freshness_hours=2, quality_threshold=1.0, notify=["product-analytics"]),
)
def saas_events(partition_date: str) -> object:
    del partition_date
    return dlt.resource(read_paginated_events(), name="saas_events")


@phlo.ingest.dlt(
    table_name="saas_account_plans",
    unique_key="account_id",
    validation_schema=AccountPlansSchema,
    group="product_analytics",
    freshness_hours=(24, 30),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=60,
    max_retries=1,
    retry_delay_seconds=1,
    add_metadata_columns=True,
    owner="revenue-operations",
    consumers=[Consumer(name="product", usage="plan segmentation")],
    sla=SLA(freshness_hours=30, quality_threshold=1.0),
)
def saas_account_plans(partition_date: str) -> object:
    del partition_date
    with (DATA_DIR / "account_plans.csv").open(newline="", encoding="utf-8") as handle:
        return dlt.resource(list(csv.DictReader(handle)), name="saas_account_plans")
