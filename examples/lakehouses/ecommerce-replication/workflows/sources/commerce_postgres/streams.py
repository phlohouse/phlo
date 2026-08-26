"""Sling replication streams from the local commerce PostgreSQL source.

Six streams exercise every replication mode the platform supports:

- ``snapshot``     customers: each run records the full source state, so the
                   lakehouse keeps customer history across source updates.
- ``incremental``  orders, order lines, payments: only rows whose ``updated_at``
                   exceeds the last replicated watermark are copied. The fixture
                   generator guarantees update rows are strictly newer.
- ``full-refresh`` products and config: small reference tables replaced
                   wholesale on each nightly run.

Asset keys are ``sling_<table_name>``; the central dbt project maps them via
``phlo_asset_key`` metadata in ``sources.yml``.
"""

from __future__ import annotations

import os

import phlo
from phlo.contracts import SLA, Consumer

DEFAULT_SOURCE_URL = "postgresql://commerce:commerce@localhost:5436/commerce?sslmode=disable"

# Target connection auto-injected by phlo-sling from the installed
# phlo-iceberg settings (Iceberg REST catalog backed by MinIO/Nessie).
ICEBERG_TARGET = "PHLO_ICEBERG"


def source_url() -> str:
    """Return the commerce source DSN.

    The default matches ``docker-compose.yml``. Override with
    ``COMMERCE_SOURCE_URL`` when the source runs elsewhere.
    """
    return os.environ.get("COMMERCE_SOURCE_URL", DEFAULT_SOURCE_URL)


@phlo.ingest.sling(
    stream_name="public.customers",
    table_name="commerce_customers",
    source_conn=source_url(),
    group="customers",
    mode="snapshot",
    primary_key="customer_id",
    freshness_hours=(168, 192),
    max_runtime_seconds=900,
    max_retries=2,
    retry_delay_seconds=60,
    owner="commerce-crm",
    consumers=[
        Consumer(name="marketing", usage="lifecycle campaigns"),
        Consumer(name="support", usage="account lookups"),
    ],
    sla=SLA(freshness_hours=192, quality_threshold=1.0),
)
def replicate_commerce_customers(context) -> dict[str, str]:
    """Weekly customer snapshot; history accumulates in the lakehouse."""
    del context
    return {"tgt_conn": ICEBERG_TARGET}


@phlo.ingest.sling(
    stream_name="public.orders",
    table_name="commerce_orders",
    source_conn=source_url(),
    group="orders",
    mode="incremental",
    primary_key="order_id",
    update_key="updated_at",
    freshness_hours=(2, 4),
    max_runtime_seconds=300,
    max_retries=5,
    retry_delay_seconds=20,
    owner="commerce-fulfillment",
    consumers=[
        Consumer(name="finance", usage="revenue recognition"),
        Consumer(name="analytics", usage="order lifecycle reporting"),
    ],
    sla=SLA(freshness_hours=4, quality_threshold=1.0),
)
def replicate_commerce_orders(context) -> dict[str, str]:
    """Frequent incremental order replication keyed on ``updated_at``."""
    del context
    return {"tgt_conn": ICEBERG_TARGET}


@phlo.ingest.sling(
    stream_name="public.order_lines",
    table_name="commerce_order_lines",
    source_conn=source_url(),
    group="orders",
    mode="incremental",
    primary_key=["order_id", "line_id"],
    update_key="updated_at",
    freshness_hours=(2, 4),
    max_runtime_seconds=300,
    max_retries=5,
    retry_delay_seconds=20,
    owner="commerce-fulfillment",
    consumers=[Consumer(name="analytics", usage="basket analysis")],
    sla=SLA(freshness_hours=4, quality_threshold=1.0),
)
def replicate_commerce_order_lines(context) -> dict[str, str]:
    """Incremental replication with a composite primary key."""
    del context
    return {"tgt_conn": ICEBERG_TARGET}


@phlo.ingest.sling(
    stream_name="public.payments",
    table_name="commerce_payments",
    source_conn=source_url(),
    group="payments",
    mode="incremental",
    primary_key="payment_id",
    update_key="updated_at",
    freshness_hours=(1, 3),
    max_runtime_seconds=300,
    max_retries=5,
    retry_delay_seconds=15,
    owner="commerce-finance",
    consumers=[
        Consumer(name="finance", usage="cash reconciliation"),
        Consumer(name="support", usage="refund investigations"),
    ],
    sla=SLA(freshness_hours=3, quality_threshold=1.0, notify=["commerce-finance"]),
)
def replicate_commerce_payments(context) -> dict[str, str]:
    """Highest-frequency incremental stream; payment corrections are upserts."""
    del context
    return {"tgt_conn": ICEBERG_TARGET}


@phlo.ingest.sling(
    stream_name="public.products",
    table_name="commerce_products",
    source_conn=source_url(),
    group="reference",
    mode="full-refresh",
    primary_key="product_id",
    freshness_hours=(26, 32),
    max_runtime_seconds=300,
    max_retries=1,
    retry_delay_seconds=60,
    owner="commerce-merchandising",
    consumers=[Consumer(name="analytics", usage="category performance")],
    sla=SLA(freshness_hours=32, quality_threshold=1.0),
)
def replicate_commerce_products(context) -> dict[str, str]:
    """Nightly full refresh of the product catalog."""
    del context
    return {"tgt_conn": ICEBERG_TARGET}


@phlo.ingest.sling(
    stream_name="public.commerce_config",
    table_name="commerce_config",
    source_conn=source_url(),
    group="reference",
    mode="full-refresh",
    primary_key="config_key",
    freshness_hours=(168, 180),
    max_runtime_seconds=120,
    max_retries=1,
    retry_delay_seconds=30,
    owner="commerce-platform",
    consumers=[Consumer(name="operations", usage="platform settings")],
    sla=SLA(freshness_hours=180, quality_threshold=1.0),
)
def replicate_commerce_config(context) -> dict[str, str]:
    """Nightly full refresh of source-side configuration key/values."""
    del context
    return {"tgt_conn": ICEBERG_TARGET}
