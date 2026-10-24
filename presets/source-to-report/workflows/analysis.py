"""The report as a Phlo asset, downstream of the dbt marts.

On the full stack the analysis reads the marts through Trino. Locally the
runner passes its DuckDB warehouse instead, so the same ``build`` function is
exercised by both paths.

This module is also what ``workflows/plugin.py`` registers under
``phlo.plugins.assets``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from phlo.capabilities.specs import AssetSpec, MaterializeResult, RunSpec

from source_to_report import paths
from source_to_report.domain import PERIOD_END
from source_to_report.warehouse import Warehouse

ANALYSIS_KEY = "air_quality_report"
MART_KEYS = ["annual_station", "claims_checked", "days_over", "monthly_station", "station_coverage"]


def analyse(runtime: Any = None, engine: Any = None) -> Iterator[MaterializeResult]:
    """Build the report from the marts in the project's query engine."""
    from source_to_report.domain.analysis import build

    if engine is None:
        from phlo_trino.resource import TrinoResource

        engine = TrinoResource()

    output = paths.default_reports_dir()
    with Warehouse(engine=engine) as warehouse:
        summary = build(warehouse, output, db_path=Path("trino"), as_of=PERIOD_END.isoformat())
    yield MaterializeResult(
        metadata={
            "report": str(output / "index.html"),
            "claims": summary["counts"]["claims"],
            "claims_differ": summary["counts"]["claims_differ"],
            "as_of": summary["as_of"],
        }
    )


ASSETS = [
    AssetSpec(
        key=ANALYSIS_KEY,
        group="monitoring",
        description="Every monitoring analysis, the charts, and the air quality brief.",
        kinds={"python", "report"},
        tags={"asset_type": "report"},
        deps=sorted(MART_KEYS),
        run=RunSpec(fn=analyse, max_runtime_seconds=900),
    ),
]
