"""Exercise consumer freshness promises on local Parquet with DuckDB and Phlo."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import duckdb
import pandas as pd
import phlo
from phlo.contracts import SLA
from phlo.governance import build_governance_surface
from phlo.logging import bind_context, clear_context, get_logger
from phlo.run_evidence import SQLiteRunEvidenceStore
from phlo_pandera import clear_quality_checks, get_quality_checks
from phlo_pandera.helpers import freshness_check_from_sla


def demonstrate(output: Path) -> None:
    """Write data and durable quality evidence, then print the observed decisions."""
    output.mkdir(parents=True, exist_ok=True)
    reference = datetime(2025, 1, 15, 12, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    cases = {
        "old_events": [reference - timedelta(hours=30)],
        "fresh_events": [reference - timedelta(hours=1)],
        "boundary": [reference - timedelta(hours=2)],
        "over_boundary": [reference - timedelta(hours=2, seconds=1)],
        "missing_column": None,
        "invalid_timestamps": ["not-a-timestamp"],
        "null_timestamps": [None],
        "empty": [],
    }
    decisions = {}
    with duckdb.connect() as connection:
        for case, timestamps in cases.items():
            row_count = len(timestamps) if timestamps is not None else 1
            frame = pd.DataFrame({"_phlo_ingested_at": [now] * row_count})
            if timestamps is not None:
                frame["event_time"] = timestamps
            path = output / f"{case}.parquet"
            frame.to_parquet(path, index=False)
            connection.execute(
                f"CREATE OR REPLACE VIEW readings AS SELECT * FROM read_parquet('{path}')"
            )
            legacy = freshness_check_from_sla(SLA(freshness_hours=2), "_phlo_ingested_at")
            assert legacy is not None
            legacy_passed = legacy.execute(
                connection.execute("SELECT * FROM readings").df(), None
            ).passed
            try:
                promise = SLA(
                    freshness_hours=2,
                    freshness_column="event_time",
                    freshness_reference_time=reference,
                )
            except TypeError:
                decisions[case] = {"legacy_passed": legacy_passed, "explicit_sla": "unavailable"}
                continue

            clear_quality_checks()
            phlo.clear_flow_declarations()

            @phlo.contract(table="readings", owner="fleet-ops", sla=promise)
            @phlo.quality.pandera(
                table="readings", checks=[], sla=promise, backend="duckdb", full_table=True
            )
            def telemetry_freshness():
                """Check the consumer promise before publishing fleet health."""

            runtime = SimpleNamespace(
                run_id=f"freshness-{case}",
                partition_key=None,
                resources={"duckdb": connection},
                logger=get_logger("freshness.demo"),
            )
            bind_context(project_id="iot-freshness", run_id=runtime.run_id)
            try:
                [check] = get_quality_checks()
                result = check.fn(runtime)
            finally:
                clear_context()
            decisions[case] = {
                "legacy_passed": legacy_passed,
                "explicit_passed": result.passed,
                "metadata": result.metadata,
                "governance_sla": build_governance_surface().table("readings").sla,
            }
    # The normal core hook sink owns persistence; reopen its SQLite database.
    store = SQLiteRunEvidenceStore(os.environ["PHLO_RUN_EVIDENCE_SQLITE_PATH"])
    for case in decisions:
        decisions[case]["stored_quality"] = store.list_quality_results(
            "iot-freshness", f"freshness-{case}"
        )
    text = json.dumps(decisions, indent=2)
    (output / "decisions.json").write_text(text + "\n")
    for case, decision in decisions.items():
        print(
            json.dumps(
                {
                    "case": case,
                    **{
                        k: v
                        for k, v in decision.items()
                        if k in {"legacy_passed", "explicit_passed", "explicit_sla"}
                    },
                    "stored_results": len(decision["stored_quality"]),
                }
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="phlo-freshness-") as temporary:
        destination = (args.output or Path(temporary)).resolve()
        os.environ["PHLO_RUN_EVIDENCE_SQLITE_PATH"] = str(destination / "evidence.sqlite")
        demonstrate(destination)
