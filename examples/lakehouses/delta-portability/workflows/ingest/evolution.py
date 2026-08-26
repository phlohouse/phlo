"""Additive schema evolution for the Delta readings table.

Firmware v2 starts emitting ``signal_quality_dbm`` at hour T06; the column is
declared as an optional field on :class:`TelemetryReadingSchema`, so one
contract covers both firmware generations and v1 batches validate unchanged.

Delta-side flow (all steps exercised by the deterministic test suite against
a local Delta warehouse):

1. Plan: :func:`plan_signal_quality_addition` diffs the live table schema
   against the contract-derived desired schema using phlo's provider-neutral
   migration planner with ``DELTA_SCHEMA_POLICY``. For a pre-v2 table the
   plan contains exactly one ``add signal_quality_dbm`` change classified
   ``safe``.
2. Apply: :func:`apply_plan_additive` executes a plan through delta-rs'
   non-destructive ``alter.add_columns``; existing rows read back NULL for
   the new column.
3. Ingest: the evolved batch appends cleanly once the column exists.

Platform gaps, recorded honestly (verified against deltalake 1.6):

- ``DeltaSchemaMigrator.diff_schema``/``apply_plan`` fail on deltalake >= 1
  (``DeltaTable.schema()`` no longer exposes ``to_pyarrow``). This module
  plans through the shared neutral planner and applies through delta-rs.
- The phlo-delta append path does not pass ``schema_mode="merge"``, so an
  evolved batch cannot be materialized until the additive column exists;
  planning plus applying the safe add is the documented operator procedure.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.request import urlopen  # noqa: S310 - replay endpoint only

import pandas as pd
from phlo.capabilities.specs import FieldSpec, NormalizedSchema, SchemaMigrationPlan

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVOLVED_CSV = PROJECT_ROOT / "generated-data" / "evolved" / "readings_v2.csv"
DEFAULT_API_URL = "http://127.0.0.1:8093/v1"

# Desired schema fields derived from the current contract, in canonical dtype
# strings understood by phlo's schema-migration planner.
CONTRACT_DTYPE_FIELDS: list[tuple[str, str]] = [
    ("message_id", "string"),
    ("device_id", "string"),
    ("site_id", "string"),
    ("sequence_number", "int64"),
    ("event_time", "timestamptz"),
    ("event_hour", "timestamptz"),
    ("ingested_from_hour", "timestamptz"),
    ("temperature_c", "float64"),
    ("humidity_pct", "float64"),
    ("battery_pct", "float64"),
    ("firmware", "string"),
    ("rssi_dbm", "int64"),
    ("signal_quality_dbm", "float64"),
    ("event_date", "string"),
]

# Canonical dtype -> delta-rs primitive name used by alter.add_columns.
_DELTA_PRIMITIVES = {
    "string": "string",
    "int32": "integer",
    "int64": "long",
    "float32": "float",
    "float64": "double",
    "bool": "boolean",
    "date": "date",
    "timestamp": "timestamp",
    "timestamptz": "timestamp",
}


def desired_contract_schema() -> NormalizedSchema:
    """Return the contract-derived normalized schema for the readings table."""
    return NormalizedSchema(
        fields=[FieldSpec(name=name, dtype=dtype) for name, dtype in CONTRACT_DTYPE_FIELDS]
    )


def _current_arrow_schema(table_name: str):
    """Read the live Delta table schema as a pyarrow schema."""
    import pyarrow as pa
    from phlo_delta.tables import _default_storage_options, _resolve_table_uri

    deltalake = __import__("deltalake")
    dt = deltalake.DeltaTable(
        _resolve_table_uri(table_name), storage_options=_default_storage_options()
    )
    return pa.schema(dt.schema().to_arrow())


def _current_normalized_fields(table_name: str) -> list[FieldSpec]:
    """Read the live table schema as normalized fields."""
    from phlo_delta.schema_migrator import _arrow_type_to_dtype

    return [
        FieldSpec(name=field.name, dtype=_arrow_type_to_dtype(field.type), nullable=True)
        for field in _current_arrow_schema(table_name)
    ]


def plan_signal_quality_addition(table_name: str = "raw.telemetry_readings") -> SchemaMigrationPlan:
    """Diff the live Delta table against the contract-derived desired schema.

    For a table created before firmware v2 the returned plan contains exactly
    one ``add signal_quality_dbm`` change classified ``safe``.
    """
    from phlo.schema_migration.planning import plan_schema_migration
    from phlo_delta.schema_migrator import DELTA_SCHEMA_POLICY

    return plan_schema_migration(
        table_name=table_name,
        current=NormalizedSchema(fields=_current_normalized_fields(table_name)),
        desired=desired_contract_schema(),
        policy=DELTA_SCHEMA_POLICY,
    )


def apply_plan_additive(table_name: str, plan: SchemaMigrationPlan) -> dict[str, object]:
    """Apply every ``add`` change of a plan without touching existing rows.

    Only additive changes are executed here by design: this helper exists to
    land optional columns safely. Delta restores dropped columns via time
    travel, but destructive changes deserve an explicit operator decision
    outside the ingestion path.
    """
    deltalake = __import__("deltalake")

    from phlo_delta.tables import _default_storage_options, _resolve_table_uri

    adds = [change for change in plan.changes if change.change_type == "add"]
    if not adds:
        return {"added_columns": []}

    fields = []
    for change in adds:
        primitive = _DELTA_PRIMITIVES.get(change.new_value or "")
        if primitive is None:
            raise ValueError(f"Unsupported additive dtype: {change.new_value!r}")
        fields.append(deltalake.schema.Field(change.field_name, primitive, nullable=True))

    dt = deltalake.DeltaTable(
        _resolve_table_uri(table_name), storage_options=_default_storage_options()
    )
    dt.alter.add_columns(fields)
    return {"added_columns": [change.field_name for change in adds]}


def fetch_evolved_batch(url: str | None = None) -> pd.DataFrame:
    """Fetch the evolved batch from the REST replay server."""
    base = url or os.environ.get("TELEMATICS_API_URL", DEFAULT_API_URL)
    with urlopen(f"{base}/readings/v2", timeout=10) as response:  # noqa: S310 - replay endpoint
        payload = json.load(response)
    return pd.DataFrame(payload["data"])


def read_evolved_batch(path: Path = EVOLVED_CSV) -> pd.DataFrame:
    """Read the evolved batch offline (replay mode)."""
    return pd.read_csv(path)
