"""Sling replication of the tenant directory from local PostgreSQL metadata.

The metadata source runs as ``chmeta-postgres`` (compose host port 10832);
see ``docker-compose.yml`` and ``scripts/seed_postgres.py``. The target is an
explicit Sling connection because phlo-sling auto-discovers
postgres/iceberg/s3 connections but not ClickHouse (platform gap, documented
in the README).
"""

from __future__ import annotations

import os

import phlo
from phlo.contracts import SLA, Consumer

DEFAULT_SOURCE_URL = "postgresql://chmeta:chmeta@localhost:10832/chmeta?sslmode=disable"

# Explicit Sling connection injected through PHLO_CLICKHOUSE_CONN (phlo.yaml
# env). Sling resolves tgt_conn names from environment variables holding JSON.
CLICKHOUSE_TARGET = "PHLO_CLICKHOUSE"


def source_url() -> str:
    """Return the tenant-metadata source DSN (overridable for tests/ops)."""
    return os.environ.get("CHMETA_SOURCE_URL", DEFAULT_SOURCE_URL)


def clickhouse_target() -> str:
    """Return the Sling connection name used to reach the raw database."""
    override = os.environ.get("CHMETA_TARGET_CONN")
    return override or CLICKHOUSE_TARGET


@phlo.ingest.sling(
    stream_name="public.tenants",
    table_name="chmeta_tenants",
    source_conn=source_url(),
    group="accounts",
    mode="snapshot",
    primary_key="tenant_id",
    freshness_hours=(26, 30),
    max_runtime_seconds=300,
    max_retries=2,
    retry_delay_seconds=60,
    owner="platform-billing",
    consumers=[
        Consumer(name="billing", usage="tenant usage attribution"),
        Consumer(name="sre", usage="tier-1 freshness screening"),
    ],
    sla=SLA(freshness_hours=30, quality_threshold=1.0),
)
def replicate_chmeta_tenants(context) -> dict[str, str]:
    """Nightly tenant snapshot; the marts join tier onto usage rollups."""
    del context
    return {"tgt_conn": clickhouse_target()}
