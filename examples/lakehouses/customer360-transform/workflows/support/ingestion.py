"""DLT ingestion of support tickets from the replay HTTP API.

``scripts/support_api.py`` serves deterministic ticket payloads from
``generated-data/support/tickets.json``. Tickets merge by ``ticket_id``, so
replays are idempotent and status updates replace in place. The full history
is small and precedence-free, so the asset opts out of partitioning.
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlencode
from urllib.request import urlopen

import dlt
import phlo
from phlo.contracts import SLA, Consumer

from workflows.schemas.customer360 import SupportTicketSchema
from workflows.support.quality import assert_resolved_after_created, assert_ticket_ids_unique

API_URL = os.environ.get("SUPPORT_API_URL", "http://127.0.0.1:8093/v1")


def fetch_tickets(url: str = API_URL) -> list[dict[str, object]]:
    """Fetch every ticket from the replay API (single deterministic page)."""
    with urlopen(f"{url}/tickets?{urlencode({'scope': 'all'})}", timeout=10) as response:  # noqa: S310 - replay endpoint
        payload = json.load(response)
    return payload["data"]


@phlo.ingest.dlt(
    table_name="support_tickets",
    unique_key="ticket_id",
    validation_schema=SupportTicketSchema,
    group="support_desk",
    quality_checks=[assert_resolved_after_created, assert_ticket_ids_unique],
    partitioned=False,
    freshness_hours=(12, 24),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=300,
    max_retries=2,
    retry_delay_seconds=60,
    add_metadata_columns=True,
    owner="support-ops",
    consumers=[
        Consumer(name="identity-resolution", usage="email variants for canonical identities"),
        Consumer(name="success-team", usage="engagement history per customer"),
    ],
    sla=SLA(freshness_hours=24, quality_threshold=1.0),
)
def support_tickets(partition_date: str) -> object:
    """Merge the replayed ticket set; replays upsert by ticket id."""
    del partition_date
    return dlt.resource(fetch_tickets(), name="support_tickets")
