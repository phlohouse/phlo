"""Load the pack's ingestion assets into a local DuckDB warehouse.

This is the generic local execution path. It calls the same ``@phlo.ingest.dlt``
assets the orchestrator runs, validates their frames against the same Pandera
contracts, and writes them with dlt to a DuckDB ``raw`` schema. Nothing here
knows what a station or a pollutant is.

The full stack replaces this module's job with ``phlo materialize``.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import dlt
import pandas as pd


def contract_columns(schema: type) -> dict[str, dict[str, Any]]:
    """Return dlt column hints from a Pandera model.

    Columns with no value in a load still exist, with the contract's type.
    """
    types = {
        "str": "text",
        "int64": "bigint",
        "Int64": "bigint",
        "float64": "double",
        "bool": "bool",
        "boolean": "bool",
    }
    return {
        name: {"data_type": types.get(str(column.dtype), "text"), "nullable": column.nullable}
        for name, column in schema.to_schema().columns.items()
    }


def asset_specs() -> tuple[tuple[Any, type, str], ...]:
    """Return (asset function, contract, raw table) in dependency order."""
    from workflows.ingestion import monitoring
    from workflows.schemas.monitoring import ClaimSchema, NoteSchema, ReadingSchema

    return (
        (monitoring.air_readings, ReadingSchema, "air_readings"),
        (monitoring.monitoring_notes, NoteSchema, "monitoring_notes"),
        (monitoring.note_claims, ClaimSchema, "note_claims"),
    )


def _quiet_library_logs() -> None:
    """Keep plugin discovery from narrating a build.

    ``phlo.logging_context.get_logger`` — used by plugin discovery — does not go
    through ``phlo.logging.setup_logging`` in Phlo 0.18.0, so it ignores the
    configured level. Filtering here keeps the preset's output to its own
    stages; warnings and errors still pass.
    """
    import logging

    import structlog

    logging.disable(logging.INFO)
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))


def load_bronze(db_path: Path, *, as_of: str, log: Callable[[str], None] = print) -> dict[str, int]:
    """Build the ``raw`` schema in ``db_path`` from the pack's assets.

    Always starts from an empty database: the pull is deterministic, so a
    partial previous build must not leak into a new one.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for path in (db_path, db_path.with_suffix(".duckdb.wal")):
        path.unlink(missing_ok=True)
    dlt_dir = db_path.parent / ".dlt"
    if dlt_dir.exists():
        shutil.rmtree(dlt_dir)

    _quiet_library_logs()

    pipeline = dlt.pipeline(
        pipeline_name="source_to_report_bronze",
        pipelines_dir=str(dlt_dir),
        destination=dlt.destinations.duckdb(str(db_path)),
        dataset_name="raw",
    )

    counts: dict[str, int] = {}
    for function, schema, table in asset_specs():
        rows = list(function(partition_date=as_of))
        if not rows:
            raise RuntimeError(f"asset {table} produced no rows; the source pull is incomplete")
        frame = pd.DataFrame(rows)
        schema.validate(frame)
        pipeline.run(
            rows,
            table_name=table,
            write_disposition="replace",
            columns=contract_columns(schema),
        )
        counts[table] = len(rows)
        log(f"  load    raw.{table:<18} {len(rows):>7,} rows  contract ok")
    return counts
