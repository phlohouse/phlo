"""DLT ingestion of scenario sensor batches for the WAP failure lab.

The pipeline reads only ``generated-data/inbound/``: run_scenario.py stages
the chosen scenario's files there before each launch. WAP launches execute
inside the Dagster service, so file-based staging (not CLI environment
variables) is what carries scenario selection across the container boundary.

Two assets share one reader and one contract:

- ``dlt_sensor_batches`` is strict: every check is blocking, writes land on
  the WAP branch, and only clean runs promote.
- ``dlt_sensor_batches_relaxed`` sets ``strict_validation=False``: identical
  checks still evaluate and fail loudly, but nothing blocks - and because the
  write branch resolution skips WAP isolation, rows reach main immediately.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import dlt
import pandas as pd
import phlo
from phlo.contracts import SLA, Consumer

from workflows.quality.validators import (
    assert_batch_ids_unique,
    assert_recordings_near_partition,
)
from workflows.retry.transient import (
    MAX_RETRIES,
    RETRY_DELAY_SECONDS,
    attempt_counter_path,
    raise_if_first_attempt,
    transient_failure_armed,
)
from workflows.schemas.contracts import SensorBatchSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INBOUND_DIR = PROJECT_ROOT / "generated-data" / "inbound"


def inbound_files(inbound_dir: Path = INBOUND_DIR) -> list[Path]:
    """Return every staged delivery file ordered by name."""
    return sorted(inbound_dir.glob("*.ndjson.gz"))


def read_batches(partition_date: str, inbound_dir: Path = INBOUND_DIR) -> pd.DataFrame:
    """Read staged deliveries restricted to one calendar partition."""
    frames: list[pd.DataFrame] = []
    suffix = f"-{partition_date}.ndjson.gz"
    for path in inbound_files(inbound_dir):
        if not path.name.endswith(suffix):
            continue
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        if rows:
            frames.append(pd.DataFrame(rows))
    if not frames:
        raise FileNotFoundError(
            f"No sensor batch deliveries found for partition '{partition_date}' under {inbound_dir}"
        )
    return pd.concat(frames, ignore_index=True)


def _maybe_inject_transient_failure() -> int:
    """Record one source attempt; fail it on the first try when armed."""
    return raise_if_first_attempt(
        counter_path=attempt_counter_path(),
        armed=transient_failure_armed(),
    )


@phlo.ingest.dlt(
    table_name="sensor_batches",
    unique_key="batch_id",
    validation_schema=SensorBatchSchema,
    group="ingest",
    quality_checks=[assert_batch_ids_unique],
    freshness_hours=(26, 30),
    merge_strategy="append",
    partition_spec=[("batch_date", "identity")],
    strict_validation=True,
    max_runtime_seconds=600,
    max_retries=MAX_RETRIES,
    retry_delay_seconds=RETRY_DELAY_SECONDS,
    add_metadata_columns=True,
    owner="sensor-platform",
    consumers=[
        Consumer(name="reliability", usage="branch lifecycle demonstrations"),
        Consumer(name="data-quality", usage="blocking check semantics"),
    ],
    sla=SLA(freshness_hours=30, quality_threshold=1.0),
)
def sensor_batches(partition_date: str) -> object:
    """Append one partition's batches on a WAP branch; promotion stays gated."""
    _maybe_inject_transient_failure()
    return dlt.resource(
        read_batches(partition_date).to_dict("records"),
        name="sensor_batches",
    )


@phlo.ingest.dlt(
    table_name="sensor_batches_relaxed",
    unique_key="batch_id",
    validation_schema=SensorBatchSchema,
    group="ingest",
    quality_checks=[assert_recordings_near_partition],
    freshness_hours=(26, 30),
    merge_strategy="append",
    partition_spec=[("batch_date", "identity")],
    strict_validation=False,
    max_retries=1,
    retry_delay_seconds=30,
    add_metadata_columns=True,
    owner="sensor-platform",
    consumers=[
        Consumer(name="data-quality", usage="warning-only check semantics"),
    ],
    sla=SLA(freshness_hours=30, quality_threshold=0.95),
)
def sensor_batches_relaxed(partition_date: str) -> object:
    """Append batches without blocking: violations log, main advances anyway."""
    return dlt.resource(
        read_batches(partition_date).to_dict("records"),
        name="sensor_batches_relaxed",
    )
