"""Build the YouTube pack end to end on DuckDB.

Same stages as the air-quality runner: generate the pull, load the assets,
run the pack's dbt models, analyse, render and record provenance. No services,
no keys.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from source_to_report import paths
from source_to_report.pipeline import run_stages
from source_to_report.youtube import PERIOD_END
from source_to_report.youtube.analysis import build
from source_to_report.youtube.sources import export_path

DEFAULT_DATABASE = "youtube.duckdb"


def _ensure_fixtures(*, regenerate: bool, quiet: bool) -> None:
    export = export_path(paths.data_dir())
    if export.is_file() and not regenerate:
        return
    script = paths.project_dir() / "scripts" / "generate_youtube_fixtures.py"
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
    """Build the channel brief and return its summary."""
    log = (lambda *_args: None) if quiet else print
    database = (db_path or paths.build_dir() / DEFAULT_DATABASE).resolve()
    output = (out_dir or paths.default_reports_dir() / "youtube").resolve()
    as_of = as_of or PERIOD_END.isoformat()

    _ensure_fixtures(regenerate=regenerate, quiet=quiet)
    _reset(output)

    def assets() -> tuple[tuple[object, type, str], ...]:
        from workflows.ingestion.youtube import asset_specs

        return asset_specs()

    counts, summary = run_stages(
        database=database,
        output=output,
        as_of=as_of,
        assets=assets,
        build_report=build,
        select="tag:youtube",
        pipeline_name="source_to_report_youtube",
        quiet=quiet,
        verbose=verbose,
    )

    report = output / "index.html"
    log("")
    log(f"  source  {counts['youtube_videos']:,} videos")
    log(f"  views   {summary['counts']['views']:,}")
    return {**summary, "report": str(report), "output": str(output), "database": str(database)}


def main(argv: list[str] | None = None) -> int:
    """Build the channel brief and print where it landed."""
    parser = argparse.ArgumentParser(
        prog="youtube-insights",
        description="Build the YouTube channel brief through the same pipeline as the air pack.",
    )
    parser.add_argument(
        "--db", type=Path, help="DuckDB file to build (default: build/youtube.duckdb)"
    )
    parser.add_argument("--out", type=Path, help="output directory (default: reports/youtube/)")
    parser.add_argument("--as-of", help="data date recorded in the report")
    parser.add_argument(
        "--regenerate-fixtures",
        action="store_true",
        help="rewrite the deterministic channel export even when it exists",
    )
    parser.add_argument("--quiet", action="store_true", help="only print the report path")
    parser.add_argument("--verbose", action="store_true", help="show Phlo's plugin discovery logs")
    args = parser.parse_args(argv)

    if not args.verbose:
        logging.disable(logging.INFO)
        os.environ["PHLO_PHLO_LOG_LEVEL"] = "ERROR"

    summary = run(
        db_path=args.db,
        out_dir=args.out,
        as_of=args.as_of,
        regenerate=args.regenerate_fixtures,
        quiet=args.quiet,
        verbose=args.verbose,
    )
    print(summary["report"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
