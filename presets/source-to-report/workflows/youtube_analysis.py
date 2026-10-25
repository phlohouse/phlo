"""The YouTube brief as a Phlo asset, downstream of its dbt marts.

Same shape as ``workflows/analysis.py``: the orchestrator passes its Trino
engine, the local runner passes DuckDB, and both call the same pack build.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from phlo.capabilities.specs import AssetSpec, MaterializeResult, RunSpec

from source_to_report import paths
from source_to_report.warehouse import Warehouse
from source_to_report.youtube import PERIOD_END

ANALYSIS_KEY = "youtube_report"
MART_KEYS = ["youtube_formats", "youtube_monthly", "youtube_videos"]


def analyse(runtime: Any = None, engine: Any = None) -> Iterator[MaterializeResult]:
    """Build the channel brief from the YouTube marts."""
    from source_to_report.youtube.analysis import build

    if engine is None:
        from phlo_trino.resource import TrinoResource

        engine = TrinoResource()

    output = paths.default_reports_dir() / "youtube"
    with Warehouse(engine=engine) as warehouse:
        summary = build(warehouse, output, db_path=Path("trino"), as_of=PERIOD_END.isoformat())
    yield MaterializeResult(
        metadata={
            "report": str(output / "index.html"),
            "videos": summary["counts"]["videos"],
            "views": summary["counts"]["views"],
            "as_of": summary["as_of"],
        }
    )


ASSETS = [
    AssetSpec(
        key=ANALYSIS_KEY,
        group="youtube",
        description="The channel brief, its charts, and the ranked video list.",
        kinds={"python", "report"},
        tags={"asset_type": "report"},
        deps=sorted(MART_KEYS),
        run=RunSpec(fn=analyse, max_runtime_seconds=900),
    ),
]
