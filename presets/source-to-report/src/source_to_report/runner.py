"""Build the preset end to end on DuckDB.

``uv run source-to-report`` calls this: generate the deterministic pull if it
is missing, load the assets, run dbt, analyse, render, and record provenance.
No services, no keys. The same assets and dbt project run on the full stack.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from source_to_report import paths
from source_to_report.dbt import run as run_dbt
from source_to_report.domain import PERIOD_END
from source_to_report.domain.analysis import build
from source_to_report.ingest import load_bronze
from source_to_report.warehouse import Warehouse


def _ensure_fixtures(*, regenerate: bool, quiet: bool) -> None:
    """Write the deterministic pull when it is missing or explicitly rebuilt."""
    readings = paths.data_dir() / "raw" / "readings.json"
    if readings.is_file() and not regenerate:
        return
    script = paths.project_dir() / "scripts" / "generate_fixtures.py"
    result = subprocess.run(
        [sys.executable, str(script), "--data-dir", str(paths.data_dir())],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"fixture generation failed:\n{result.stderr or result.stdout}")
    if not quiet:
        print(result.stdout.rstrip())


def _reset(out_dir: Path) -> None:
    """Replace the output directory, refusing to delete anything above the project."""
    root = paths.project_dir().resolve()
    resolved = out_dir.resolve()
    if resolved == root or resolved in root.parents or resolved == Path("/"):
        raise ValueError(f"refusing to clear output directory {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)


def run(
    *,
    db_path: Path | None = None,
    out_dir: Path | None = None,
    as_of: str | None = None,
    regenerate: bool = False,
    quiet: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Build the lakehouse and return the report's summary."""
    log = (lambda *_args: None) if quiet else print
    database = (db_path or paths.build_dir() / "source_to_report.duckdb").resolve()
    output = (out_dir or paths.default_reports_dir()).resolve()
    as_of = as_of or PERIOD_END.isoformat()

    _ensure_fixtures(regenerate=regenerate, quiet=quiet)
    _reset(output)

    counts = load_bronze(database, as_of=as_of, log=log)
    run_dbt(database, quiet=not verbose, log=log)
    with Warehouse(duckdb_path=database) as warehouse:
        summary = build(warehouse, output, db_path=database, as_of=as_of)

    report = output / "index.html"
    log("")
    log(f"  source  {counts['air_readings']:,} readings, {counts['monitoring_notes']} notes")
    log(f"  claims  {counts['note_claims']} published figures checked")
    return {**summary, "report": str(report), "output": str(output), "database": str(database)}
