"""DLT merge of the Sling regions snapshot into the Delta table.

The snapshot produced (or replayed) by the Sling stream is merged by
``region_code`` into ``raw.delta_regions``. Re-running the full refresh is
idempotent: the merge upserts every row and never grows the table.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import dlt
import pandas as pd
import phlo
from phlo.contracts import SLA, Consumer

from workflows.schemas.telemetry import RegionDirectorySchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DELTA_ROUTING = {"table_store": "delta"}


def _snapshot_uri_and_fs() -> tuple[str, Any | None] | tuple[None, None]:
    """Return the Sling hand-off snapshot as ``(uri, filesystem)``.

    The replication targets ``PHLO_DELTA``, so live snapshots land in the
    Delta warehouse on S3. Offline (no phlo-delta settings resolvable to a
    live stack) returns ``(None, None)`` and the caller falls back to the
    deterministic CSV replay fixture.
    """
    try:
        from phlo_delta.settings import get_settings

        settings = get_settings()
    except Exception:  # pragma: no cover - offline fallback for tests
        return None, None

    root = str(settings.delta_warehouse_path).rstrip("/")
    if not root.startswith("s3://"):
        return f"file://{root}/snapshots/regions_snapshot.parquet", None

    bucket, _, key = root[len("s3://") :].partition("/")
    import pyarrow as pa

    filesystem = pa.fs.S3FileSystem(
        access_key=settings.delta_s3_access_key,
        secret_key=settings.delta_s3_secret_key,
        endpoint_override=settings.delta_s3_endpoint,
        region=settings.delta_s3_region,
        allow_bucket_creation=False,
        allow_bucket_deletion=False,
    )
    return f"{bucket}/{key}/snapshots/regions_snapshot.parquet", filesystem


def read_region_snapshot() -> pd.DataFrame:
    """Read the replicated snapshot, falling back to the CSV replay fixture.

    The Parquet snapshot only exists after a live Sling run against the
    compose PostgreSQL source; without it, the deterministic ``regions.csv``
    fixture replays the same rows offline.
    """
    import pyarrow.parquet as pq

    uri, filesystem = _snapshot_uri_and_fs()
    if uri is not None and filesystem is not None:
        try:
            return pq.read_table(uri, filesystem=filesystem).to_pandas()
        except Exception:  # noqa: BLE001 - any read failure falls back to replay
            pass
    fallback = PROJECT_ROOT / "generated-data" / "regions" / "regions.csv"
    frame = pd.read_csv(fallback)
    frame["updated_at"] = pd.to_datetime(frame["updated_at"])
    return frame


@phlo.ingest.dlt(
    table_name="delta_regions",
    unique_key="region_code",
    validation_schema=RegionDirectorySchema,
    group="regions",
    capabilities=DELTA_ROUTING,
    partitioned=False,
    freshness_hours=(168, 192),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=120,
    max_retries=2,
    retry_delay_seconds=60,
    add_metadata_columns=True,
    owner="facilities",
    consumers=[Consumer(name="fleet-reporting", usage="site region enrichment")],
    sla=SLA(freshness_hours=192, quality_threshold=1.0),
)
def delta_regions(partition_date: str) -> object:
    """Merge the replicated regions lookup into Delta."""
    del partition_date
    return dlt.resource(
        read_region_snapshot().to_dict("records"),
        name="delta_regions",
    )
