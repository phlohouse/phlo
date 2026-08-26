"""DLT ingestion of late correction deliveries merged into Delta.

Corrections arrive after their target readings and merge by ``message_id``,
so replaying a correction batch never grows the table. Delta Lake implements
the upsert natively (delta-rs MERGE), which is exactly the behavior the
portability comparison wants to prove: applying the same corrections twice
leaves row counts unchanged.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import dlt
import pandas as pd
import phlo
from phlo.contracts import SLA, Consumer

from workflows.schemas.telemetry import TelemetryCorrectionSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CORRECTIONS_DIR = PROJECT_ROOT / "generated-data" / "telemetry" / "corrections"
DELTA_ROUTING = {"table_store": "delta"}


def read_corrections(corrections_dir: Path = CORRECTIONS_DIR) -> pd.DataFrame:
    """Read every correction file into one frame."""
    frames: list[pd.DataFrame] = []
    for path in sorted(corrections_dir.glob("*.ndjson.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        if rows:
            frames.append(pd.DataFrame(rows))
    if not frames:
        raise FileNotFoundError(f"No correction deliveries found under {corrections_dir}")
    return pd.concat(frames, ignore_index=True)


def assert_single_correction_per_message(corrections: pd.DataFrame) -> str | None:
    """One correction delivery must not amend the same message twice."""
    duplicated = corrections.message_id[corrections.message_id.duplicated()]
    if not duplicated.empty:
        offenders = sorted(duplicated.unique().tolist())[:5]
        return f"message_id corrected more than once per delivery: {offenders}"
    return None


@phlo.ingest.dlt(
    table_name="telemetry_corrections",
    unique_key="message_id",
    validation_schema=TelemetryCorrectionSchema,
    group="ingest",
    capabilities=DELTA_ROUTING,
    quality_checks=[assert_single_correction_per_message],
    freshness_hours=(26, 30),
    merge_strategy="merge",
    strict_validation=True,
    max_runtime_seconds=300,
    max_retries=3,
    retry_delay_seconds=60,
    add_metadata_columns=True,
    owner="fleet-operations",
    consumers=[Consumer(name="reliability", usage="late-event repair of aggregates")],
    sla=SLA(freshness_hours=30, quality_threshold=1.0),
)
def telemetry_corrections(partition_date: str) -> object:
    """Merge late corrections onto previously ingested message ids."""
    del partition_date
    return dlt.resource(
        read_corrections().to_dict("records"),
        name="telemetry_corrections",
    )
