"""Sling replication of the PostgreSQL regions lookup for the Delta example.

The compose file in this directory runs PostgreSQL on host port 10732
(user/password/database ``delta``) and ``scripts/seed_postgres.py`` loads the
``public.regions`` lookup.

The replication full-refreshes into a Parquet hand-off snapshot under
``<warehouse>/snapshots/`` that the ``delta_regions`` ingestion asset merges
into the Delta table through the provider-neutral table-store interface.
Re-running is idempotent: full-refresh rewrites the snapshot in place.

The snapshot targets the ``PHLO_DELTA`` auto-connection (an S3 connection
rooted at the Delta warehouse, carrying endpoint and credentials from the
Delta settings), so the hand-off lands inside the warehouse next to the
Delta tables it feeds.
"""

from __future__ import annotations

import os

import phlo
from phlo.contracts import SLA, Consumer

DEFAULT_SOURCE_URL = "postgresql://delta:delta@localhost:10732/delta?sslmode=disable"


def source_url() -> str:
    """Return the regions source DSN."""
    return os.environ.get("REGIONS_SOURCE_URL", DEFAULT_SOURCE_URL)


@phlo.ingest.sling(
    stream_name="public.regions",
    table_name="delta_regions_snapshot",
    source_conn=source_url(),
    group="regions",
    mode="full-refresh",
    primary_key="region_code",
    freshness_hours=(168, 192),
    max_runtime_seconds=300,
    max_retries=2,
    retry_delay_seconds=60,
    owner="facilities",
    consumers=[Consumer(name="fleet-reporting", usage="site region enrichment")],
    sla=SLA(freshness_hours=192, quality_threshold=1.0),
)
def replicate_delta_regions_snapshot(context) -> dict[str, str]:
    """Full-refresh the regions lookup into a Parquet hand-off snapshot."""
    del context
    # Sling resolves relative objects against the process working directory,
    # so the warehouse-qualified path is passed explicitly.
    from phlo_delta.settings import get_settings

    warehouse = get_settings().delta_warehouse_path.rstrip("/")
    return {
        "tgt_conn": "PHLO_DELTA",
        "tgt_object": f"{warehouse}/snapshots/regions_snapshot.parquet",
    }
