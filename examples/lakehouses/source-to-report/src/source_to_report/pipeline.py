"""Run a pack through the shared stages.

Both packs use this: load the pack's assets into a fresh DuckDB warehouse,
run the pack's dbt models, then hand the warehouse to the pack's analysis.
The only differences are the arguments.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from source_to_report.dbt import run as run_dbt
from source_to_report.ingest import load_assets, quiet_library_logs
from source_to_report.warehouse import Warehouse


def run_stages(
    *,
    database: Path,
    output: Path,
    as_of: str,
    assets: Callable[[], tuple[tuple[Any, type, str], ...]],
    build_report: Callable[..., dict[str, Any]],
    select: str | None = None,
    exclude: str | None = None,
    pipeline_name: str = "source_to_report_bronze",
    quiet: bool = False,
    verbose: bool = False,
) -> tuple[dict[str, int], dict[str, Any]]:
    """Load, transform and analyse one pack. Returns its counts and summary."""
    log = (lambda *_args: None) if quiet else print
    quiet_library_logs()
    counts = load_assets(
        assets(),
        database,
        as_of=as_of,
        log=log,
        pipeline_name=pipeline_name,
    )
    run_dbt(
        database,
        quiet=not verbose,
        log=log,
        select=select,
        exclude=exclude,
    )
    with Warehouse(duckdb_path=database) as warehouse:
        summary = build_report(warehouse, output, db_path=database, as_of=as_of)
    return counts, summary
