"""Sling incremental replication of the logistics PostgreSQL orders source.

The source runs as a local container (``docker-compose.yml``, port 10332) and
is seeded from deterministic fixtures by ``scripts/seed_postgres.py``. Sling's
Iceberg target is append-only for incremental mode, so updated rows arrive as
additional versions keyed by ``updated_at``; the orders transform collapses
them read-time.
"""

from __future__ import annotations

import os

import phlo
from phlo.contracts import SLA, Consumer

DEFAULT_SOURCE_URL = "postgresql://logistics:logistics@localhost:10332/logistics?sslmode=disable"

# Target connection auto-injected by phlo-sling from the installed
# phlo-iceberg settings (Iceberg REST catalog backed by MinIO/Nessie).
ICEBERG_TARGET = "PHLO_ICEBERG"


def source_url() -> str:
    """Return the logistics source DSN; override with LOGISTICS_SOURCE_URL."""
    return os.environ.get("LOGISTICS_SOURCE_URL", DEFAULT_SOURCE_URL)


@phlo.ingest.sling(
    stream_name="public.orders",
    table_name="shipments_orders",
    source_conn=source_url(),
    group="orders",
    mode="incremental",
    primary_key="order_id",
    update_key="updated_at",
    freshness_hours=(4, 6),
    max_runtime_seconds=600,
    max_retries=3,
    retry_delay_seconds=60,
    owner="logistics-fulfillment",
    consumers=[
        Consumer(name="control-tower", usage="canonical shipment state"),
        Consumer(name="support", usage="customer order lookups"),
    ],
    sla=SLA(freshness_hours=6, quality_threshold=1.0),
)
def replicate_shipment_orders(context) -> dict[str, str]:
    """Incremental order replication keyed on ``updated_at``."""
    del context
    return {"tgt_conn": ICEBERG_TARGET}
