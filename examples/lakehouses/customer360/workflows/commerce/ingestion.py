"""Sling replication of the commerce PostgreSQL source into the lakehouse.

The source runs via ``docker-compose.yml`` (port 10432). Email is the identity
key on customers, so replication upserts by ``email`` rather than a surrogate
id; case and plus-suffix variants are preserved in raw and collapsed later by
the identity-resolution model.
"""

from __future__ import annotations

import os

import phlo
from phlo.contracts import SLA, Consumer

DEFAULT_SOURCE_URL = "postgresql://commerce:commerce@localhost:10432/commerce?sslmode=disable"

# Target connection auto-injected by phlo-sling from the installed
# phlo-iceberg settings (Iceberg REST catalog backed by MinIO/Nessie).
ICEBERG_TARGET = "PHLO_ICEBERG"


def source_url() -> str:
    """Return the commerce source DSN (override with COMMERCE_SOURCE_URL)."""
    return os.environ.get("COMMERCE_SOURCE_URL", DEFAULT_SOURCE_URL)


@phlo.ingest.sling(
    stream_name="public.customers",
    table_name="c360_customers",
    source_conn=source_url(),
    group="commerce_identity",
    mode="incremental",
    primary_key="email",
    update_key="updated_at",
    freshness_hours=(24, 48),
    max_runtime_seconds=600,
    max_retries=3,
    retry_delay_seconds=60,
    owner="commerce-crm",
    consumers=[
        Consumer(name="identity-resolution", usage="canonical customer dimension"),
        Consumer(name="marketing", usage="audience overlap"),
    ],
    sla=SLA(freshness_hours=48, quality_threshold=1.0),
)
def replicate_c360_customers(context) -> dict[str, str]:
    """Incremental customer replication keyed on ``updated_at``."""
    del context
    return {"tgt_conn": ICEBERG_TARGET}


@phlo.ingest.sling(
    stream_name="public.orders",
    table_name="c360_orders",
    source_conn=source_url(),
    group="commerce_orders",
    mode="incremental",
    primary_key="order_id",
    update_key="updated_at",
    freshness_hours=(6, 12),
    max_runtime_seconds=300,
    max_retries=5,
    retry_delay_seconds=30,
    owner="commerce-fulfillment",
    consumers=[
        Consumer(name="analytics", usage="order lifecycle reporting"),
        Consumer(name="support", usage="purchase context on tickets"),
    ],
    sla=SLA(freshness_hours=12, quality_threshold=1.0),
)
def replicate_c360_orders(context) -> dict[str, str]:
    """Frequent incremental order replication keyed on ``updated_at``."""
    del context
    return {"tgt_conn": ICEBERG_TARGET}
