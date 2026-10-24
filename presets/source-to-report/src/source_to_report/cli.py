"""Command line for the local runner.

This is a project script, not a Phlo CLI command. The preset deliberately
stays out of `phlo init` and the Phlo command surface.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from source_to_report.runner import run


def main(argv: list[str] | None = None) -> int:
    """Build the report and print where it landed."""
    parser = argparse.ArgumentParser(
        prog="source-to-report",
        description="Build the reference source -> insight -> analysis -> report lakehouse on DuckDB.",
    )
    parser.add_argument(
        "--db", type=Path, help="DuckDB file to build (default: build/source_to_report.duckdb)"
    )
    parser.add_argument("--out", type=Path, help="output directory (default: reports/)")
    parser.add_argument("--as-of", help="data date recorded in the report")
    parser.add_argument(
        "--regenerate-fixtures",
        action="store_true",
        help="rewrite the deterministic pull even when data/raw/readings.json exists",
    )
    parser.add_argument("--quiet", action="store_true", help="only print the report path")
    parser.add_argument("--verbose", action="store_true", help="show Phlo's plugin discovery logs")
    args = parser.parse_args(argv)

    if not args.verbose:
        logging.disable(logging.INFO)
        # Phlo's settings use the `PHLO_` prefix and the field is `phlo_log_level`.
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
