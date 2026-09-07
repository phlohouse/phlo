"""Drive one WAP failure-lab scenario against a running Phlo stack.

Usage (from this directory, platform stack running):

    uv run python scripts/run_scenario.py <scenario> [--timeout 900]

The runner stages the scenario's fixture files into ``generated-data/inbound``,
launches ``phlo materialize`` per step, polls ``.phlo/wap-reports/*.json``
(schema ``phlo.wap_report.v2``) for terminal evidence, and asserts catalog
branch state via ``phlo_nessie`` plus row counts via Trino.

Report-classification helpers are stdlib-only so the deterministic test suite
can exercise them against synthetic payloads without containers.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORTS_DIR = ROOT / ".phlo" / "wap-reports"
SCENARIOS_DIR = ROOT / "generated-data" / "scenarios"
INBOUND_DIR = ROOT / "generated-data" / "inbound"

REPORT_SCHEMA_VERSION = "phlo.wap_report.v2"
WAP_BRANCH_PREFIX = "pipeline-run-"
STRICT_ASSET = "dlt_sensor_batches"
RELAXED_ASSET = "dlt_sensor_batches_relaxed"
STRICT_TABLE = "sensor_batches"
RELAXED_TABLE = "sensor_batches_relaxed"

PROMOTED_ROWS = 12
NULL_FAILURE_ROWS = 6
DUPLICATE_FAILURE_ROWS = 7
RETRY_ROWS = 10
SCHEMA_ROWS = 8
CONCURRENT_A_ROWS = 12
CONCURRENT_B_ROWS = 8
WARNING_ROWS = 7


class ScenarioError(AssertionError):
    """Raised when an observed scenario outcome violates its contract."""


# ---------------------------------------------------------------------------
# Report parsing (stdlib-only, container-free)


def load_report(path: Path) -> dict:
    """Load one WAP report JSON document."""
    return json.loads(path.read_text(encoding="utf-8"))


def list_reports(reports_dir: Path = DEFAULT_REPORTS_DIR) -> dict[str, dict]:
    """Return every top-level WAP report keyed by logical run id."""
    reports: dict[str, dict] = {}
    if not reports_dir.is_dir():
        return reports
    for path in sorted(reports_dir.glob("*.json")):
        try:
            payload = load_report(path)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            reports[path.stem] = payload
    return reports


def classify_report(payload: dict | None) -> str:
    """Collapse a WAP report onto the lab's four-state outcome model.

    Returns ``promoted``, ``blocked``, ``failed``, ``in_flight`` or
    ``missing``. ``failed`` covers ``status="failed"`` and any report whose
    ``failure_reason`` is ``dagster_run_failed``; the promotion sensor
    terminalizes failed Dagster runs this way (live-proven 2026-09-03), so a
    genuine run-level failure reaches the ``failed`` classification instead of
    lingering ``in_flight``.
    """
    if payload is None:
        return "missing"
    status = str(payload.get("status", ""))
    failure_reason = payload.get("failure_reason")
    if status == "promoted":
        return "promoted"
    if status in {"promotion_blocked", "promotion_failed"}:
        return "blocked"
    if status == "failed" or failure_reason == "dagster_run_failed":
        return "failed"
    return "in_flight"


def wait_for_terminal_report(
    logical_run_id: str,
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    timeout_seconds: float = 600.0,
    poll_seconds: float = 5.0,
) -> tuple[str, str, dict | None]:
    """Wait only for the exact logical run returned by the launch command."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        payload = list_reports(reports_dir).get(logical_run_id)
        classification = classify_report(payload)
        if classification in {"promoted", "blocked", "failed"}:
            return classification, logical_run_id, payload
        if time.monotonic() >= deadline:
            return classification, logical_run_id, payload
        time.sleep(poll_seconds)


def report_ids_for_branch(branch: str, reports_dir: Path = DEFAULT_REPORTS_DIR) -> list[str]:
    """Return logical run ids whose report names the given branch."""
    return [
        run_id
        for run_id, payload in list_reports(reports_dir).items()
        if payload.get("branch") == branch
    ]


# ---------------------------------------------------------------------------
# Live-stack access (lazy imports; never exercised by pytest)


def stage_inbound(scenario: str) -> list[Path]:
    """Copy one scenario's delivery files into the pipeline-visible inbound dir."""
    source_dir = SCENARIOS_DIR / scenario
    files = sorted(source_dir.glob("*.ndjson.gz"))
    if not files:
        raise ScenarioError(f"scenario '{scenario}' has no staged fixtures under {source_dir}")
    if INBOUND_DIR.exists():
        shutil.rmtree(INBOUND_DIR)
    INBOUND_DIR.mkdir(parents=True)
    staged = []
    for path in files:
        target = INBOUND_DIR / path.name
        shutil.copyfile(path, target)
        staged.append(target)
    return staged


def trino_fetchall(query: str) -> list[tuple]:
    """Run one read-only query against Trino."""
    import trino  # noqa: PLC0415 - container-dependent import kept lazy

    connection = trino.dbapi.connect(
        host=os.getenv("TRINO_HOST", "localhost"),
        port=int(os.getenv("TRINO_PORT", "8080")),
        user="wap-failure-lab",
        catalog="iceberg",
    )
    try:
        cursor = connection.cursor()
        cursor.execute(query)
        return cursor.fetchall()
    finally:
        connection.close()


def table_count(table: str, where: str = "1=1") -> int:
    """Count published rows in one raw table; a missing object counts as zero."""
    import trino  # noqa: PLC0415 - container-dependent import kept lazy

    try:
        rows = trino_fetchall(f"SELECT count(*) FROM iceberg.raw.{table} WHERE {where}")
    except trino.exceptions.TrinoUserError as exc:
        if "does not exist" not in str(exc):
            raise
        return 0
    return int(rows[0][0])


def table_columns(table: str) -> list[str]:
    """List physical column names of one raw table."""
    rows = trino_fetchall(
        "SELECT column_name FROM iceberg.information_schema.columns "
        f"WHERE table_schema = 'raw' AND table_name = '{table}' ORDER BY ordinal_position"
    )
    return [str(row[0]) for row in rows]


def nessie_client():
    """Build a Nessie client honouring NESSIE_URL when provided."""
    from phlo_nessie.resource import NessieResource  # noqa: PLC0415

    base_url = os.getenv("NESSIE_URL")
    return NessieResource(base_url) if base_url else NessieResource()


def branch_hash(branch: str) -> str | None:
    """Return the current hash of a catalog branch, or None when absent."""
    return nessie_client().get_branch_hash(branch)


def pipeline_branch_names() -> list[str]:
    """List every WAP-owned branch currently present in the catalog."""
    return [
        info.name
        for info in nessie_client().list_branches()
        if info.name.startswith(WAP_BRANCH_PREFIX)
    ]


def materialize(asset: str, partition: str, timeout_seconds: float = 300.0) -> str:
    """Submit asynchronously and return its exact logical run identity."""
    command = [
        os.getenv("PHLO_EXECUTABLE", "phlo"),
        "materialize",
        asset,
        "--partition",
        partition,
        "--json",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    output = completed.stdout + completed.stderr
    if completed.returncode != 0:
        raise ScenarioError(f"materialize {' '.join(command)} failed:\n{output}")
    try:
        run_id = json.loads(completed.stdout)["data"]["logical_run_id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ScenarioError(f"materialize returned no logical run identity: {output}") from exc
    _require(isinstance(run_id, str) and bool(run_id), "empty logical run identity")
    return run_id


# ---------------------------------------------------------------------------
# Scenario context


@dataclass
class LabContext:
    """Shared knobs for scenario runners."""

    reports_dir: Path = DEFAULT_REPORTS_DIR
    promote_timeout: float = 900.0
    failure_timeout: float = 180.0

    def baseline(self) -> set[str]:
        return set(list_reports(self.reports_dir))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ScenarioError(message)


def _require_promoted(payload: dict | None, branch_hint: str) -> None:
    classification = classify_report(payload)
    _require(
        classification == "promoted",
        f"{branch_hint}: expected promoted report, observed {classification}: {payload}",
    )
    _require(
        payload.get("target_hash_after") != payload.get("target_hash_before"),
        f"{branch_hint}: promotion did not move main "
        f"(before={payload.get('target_hash_before')} after={payload.get('target_hash_after')})",
    )


# ---------------------------------------------------------------------------
# Scenario runners


def assert_run_evidence(payload: dict, run_id: str) -> None:
    """Require observed ingestion evidence for the exact successful attempt."""
    _require(payload.get("run_id") == run_id, "exported evidence belongs to another run")
    _require(
        payload.get("terminal_outcome", {}).get("status") == "success",
        "exported evidence has no successful terminal outcome",
    )
    for field in (
        "inputs",
        "staging",
        "outputs",
        "lineage",
        "artifacts",
        "iceberg_snapshots",
        "quality",
        "catalog_changes",
    ):
        _require(bool(payload.get(field)), f"exported evidence is missing {field}")
    for field in ("inputs", "staging", "outputs"):
        _require(
            all(item.get("resource_identity_status") == "complete" for item in payload[field]),
            f"{field} contains incomplete identities",
        )


def export_and_check_run_evidence(run_id: str, reports_dir: Path) -> None:
    """Export durable ingestion evidence; separate dbt execution is not correlated."""
    from dataclasses import asdict  # noqa: PLC0415

    from phlo.run_evidence.report import build_run_report  # noqa: PLC0415
    from phlo.run_evidence.store import default_run_evidence_store  # noqa: PLC0415

    report = build_run_report(
        default_run_evidence_store(), os.getenv("PHLO_PROJECT", "wap-failure-lab"), run_id, 1
    )
    payload = asdict(report)
    output_dir = reports_dir.parent / "lab-run-evidence"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{run_id}.json").write_text(json.dumps(payload, indent=2, default=str))
    assert_run_evidence(payload, run_id)


def build_and_check_dbt() -> None:
    """Build/test the downstream model, then compare all aggregates with main."""
    project = ROOT / "workflows" / "transforms" / "dbt"
    subprocess.run(
        [
            os.getenv("DBT_EXECUTABLE", "dbt"),
            "build",
            "--project-dir",
            str(project),
            "--profiles-dir",
            str(project / "profiles"),
        ],
        cwd=ROOT,
        check=True,
        timeout=600,
    )
    expected = trino_fetchall(
        "SELECT sensor_id, count(*), count(DISTINCT batch_id) "
        "FROM iceberg.raw.sensor_batches GROUP BY sensor_id ORDER BY sensor_id"
    )
    observed = trino_fetchall(
        "SELECT sensor_id, batch_count, distinct_batches "
        "FROM iceberg.raw.batch_summary ORDER BY sensor_id"
    )
    _require(bool(expected) and observed == expected, "dbt aggregate differs from published main")


def run_valid_publish(ctx: LabContext) -> dict:
    """Clean batch promotes atomically; branch removed after promotion."""
    stage_inbound("valid_publish")
    before_rows = table_count(STRICT_TABLE)
    launched_id = materialize(STRICT_ASSET, "2026-08-20")
    classification, run_id, payload = wait_for_terminal_report(
        launched_id, ctx.reports_dir, ctx.promote_timeout
    )
    _require(classification == "promoted", f"valid_publish ended {classification}")
    _require_promoted(payload, "valid_publish")
    after_rows = table_count(STRICT_TABLE)
    branch = payload["branch"]
    summary = {
        "scenario": "valid_publish",
        "rows_added": after_rows - before_rows,
        "branch": branch,
        "branch_present_after": branch_hash(branch) is not None,
        "run_id": run_id,
    }
    _require(
        after_rows - before_rows == PROMOTED_ROWS, f"expected +{PROMOTED_ROWS} rows, saw {summary}"
    )
    _require(not summary["branch_present_after"], f"branch {branch} survived promotion")
    export_and_check_run_evidence(run_id, ctx.reports_dir)
    build_and_check_dbt()
    summary["dbt_build_and_sql_verified"] = True
    return summary


def run_quality_failure(ctx: LabContext) -> dict:
    """Strict contract failure leaves main unchanged and retains audit evidence."""
    stage_inbound("quality_failure")
    before_rows = table_count(STRICT_TABLE)
    before_hash = branch_hash("main")
    launched_id = materialize(STRICT_ASSET, "2026-08-20")
    classification, run_id, payload = wait_for_terminal_report(
        launched_id, ctx.reports_dir, ctx.failure_timeout
    )
    after_rows = table_count(STRICT_TABLE)
    after_hash = branch_hash("main")
    branch = (payload or {}).get("branch", "")
    summary = {
        "scenario": "quality_failure",
        "classification": classification,
        "rows_before": before_rows,
        "rows_after": after_rows,
        "report_retained": bool(payload),
        "dagster_run_id": (payload or {}).get("dagster_run_id"),
        "schema_version": (payload or {}).get("schema_version"),
        "branch": branch,
        "branch_present_for_audit": bool(branch) and branch_hash(branch) is not None,
    }
    _require(
        classification in {"blocked", "failed"}, f"quality_failure did not terminalize: {payload}"
    )
    _require(after_rows == before_rows, "main advanced during a failed strict run")
    _require(after_hash == before_hash, "main ref moved during a failed strict run")
    _require(bool(payload), "no WAP report was written for the failed run")
    _require(
        payload.get("schema_version") == REPORT_SCHEMA_VERSION,
        f"unexpected report schema {payload.get('schema_version')}",
    )
    _require(bool(summary["branch_present_for_audit"]), "violating branch was cleaned up")
    return summary


def run_retry_recovery(ctx: LabContext) -> dict:
    """Armed transient failure recovers on attempt two and promotes."""
    from workflows.retry.transient import (  # noqa: PLC0415
        attempt_counter_path,
        read_attempts,
        reset_retry_state,
    )

    reset_retry_state()
    counter_path = attempt_counter_path()
    counter_path.parent.mkdir(parents=True, exist_ok=True)
    arm_marker = counter_path.parent / "retry-arm"
    arm_marker.write_text("armed\n", encoding="utf-8")

    stage_inbound("retry_recovery")
    before_rows = table_count(STRICT_TABLE)
    launched_id = materialize(STRICT_ASSET, "2026-08-22")
    classification, run_id, payload = wait_for_terminal_report(
        launched_id, ctx.reports_dir, ctx.promote_timeout
    )
    attempts = read_attempts(counter_path)
    after_rows = table_count(STRICT_TABLE)
    summary = {
        "scenario": "retry_recovery",
        "classification": classification,
        "attempts_recorded": attempts,
        "rows_added": after_rows - before_rows,
        "run_id": run_id,
    }
    _require_promoted(payload, "retry_recovery")
    _require(attempts == 2, f"expected 2 recorded attempts, saw {attempts}")
    _require(after_rows - before_rows == RETRY_ROWS, f"expected +{RETRY_ROWS} rows, saw {summary}")
    _require(branch_hash(payload["branch"]) is None, "retry run left its branch behind")
    return summary


def run_schema_change(ctx: LabContext) -> dict:
    """Additive optional column migrates without disturbing old readers."""
    stage_inbound("schema_change")
    columns_before = table_columns(STRICT_TABLE)
    old_rows_before = table_count(STRICT_TABLE, "_phlo_partition_date < '2026-08-23'")
    launched_id = materialize(STRICT_ASSET, "2026-08-23")
    classification, run_id, payload = wait_for_terminal_report(
        launched_id, ctx.reports_dir, ctx.promote_timeout
    )
    columns_after = table_columns(STRICT_TABLE)
    old_null_scores = table_count(
        STRICT_TABLE,
        "_phlo_partition_date < '2026-08-23' AND reading_quality_score IS NULL",
    )
    new_partition_rows = table_count(STRICT_TABLE, "_phlo_partition_date = '2026-08-23'")
    summary = {
        "scenario": "schema_change",
        "classification": classification,
        "columns_before": len(columns_before),
        "columns_after": len(columns_after),
        "new_column": "reading_quality_score" in columns_after,
        "old_rows_with_null_score": old_null_scores,
        "old_rows_total": old_rows_before,
        "new_partition_rows": new_partition_rows,
        "run_id": run_id,
    }
    _require_promoted(payload, "schema_change")
    _require(summary["new_column"], "reading_quality_score never appeared in the table")
    _require(
        "reading_quality_score" in columns_after,
        "schema_change did not add reading_quality_score",
    )
    # The optional column may already exist; append writes are not idempotent.
    _require(
        len(columns_after) - len(columns_before) in (0, 1),
        f"unexpected column delta: {len(columns_before)} -> {len(columns_after)}",
    )
    _require(old_null_scores == old_rows_before, "pre-change rows were rewritten")
    _require(new_partition_rows == SCHEMA_ROWS, f"expected {SCHEMA_ROWS} schema-change rows")
    _require(
        table_count(
            STRICT_TABLE,
            "_phlo_partition_date = '2026-08-23' AND reading_quality_score IS NOT NULL",
        )
        == SCHEMA_ROWS,
        "new schema rows are missing their scores",
    )
    return summary


def run_concurrent_runs(ctx: LabContext) -> dict:
    """Overlap submissions; successful writes publish, conflicts retain recovery branches."""
    stage_inbound("concurrent_runs")
    before_rows = table_count(STRICT_TABLE)
    existing = Counter(map(tuple, trino_fetchall(f"SELECT * FROM iceberg.raw.{STRICT_TABLE}")))
    predicates = ["batch_id LIKE 'b-60%'", "batch_id LIKE 'b-70%'"]
    before = [table_count(STRICT_TABLE, predicate) for predicate in predicates]
    # Both files remain staged until both runs finish. Submit both before polling.
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(materialize, STRICT_ASSET, partition)
            for partition in ("2026-08-20", "2026-08-21")
        ]
        launches = [future.result() for future in futures]
    _require(launches[0] != launches[1], "launch identities collided")
    outcomes = [
        wait_for_terminal_report(run_id, ctx.reports_dir, ctx.promote_timeout)
        for run_id in launches
    ]
    launch_bases = [(payload or {}).get("launch_target_hash_before") for _, _, payload in outcomes]
    _require(
        all(isinstance(base, str) and base for base in launch_bases)
        and len(set(launch_bases)) == 1,
        "runs did not overlap on the same main revision; repeat the concurrency scenario",
    )
    branches = []
    expected_total = 0
    for index, ((classification, run_id, payload), size) in enumerate(
        zip(outcomes, (CONCURRENT_A_ROWS, CONCURRENT_B_ROWS), strict=True)
    ):
        _require(
            classification in {"promoted", "blocked", "failed"},
            f"{run_id}: no terminal evidence ({classification})",
        )
        branch = (payload or {}).get("branch")
        _require(bool(branch), f"{run_id}: missing branch evidence")
        branches.append(branch)
        published = classification == "promoted"
        if published:
            _require_promoted(payload, run_id)
        _require(
            (branch_hash(branch) is None) == published,
            f"{run_id}: cleanup does not match publication outcome",
        )
        delta = table_count(STRICT_TABLE, predicates[index]) - before[index]
        expected = size if published else 0
        _require(delta == expected, f"{run_id}: expected {expected} rows, observed {delta}")
        expected_total += expected
    remaining = Counter(map(tuple, trino_fetchall(f"SELECT * FROM iceberg.raw.{STRICT_TABLE}")))
    _require(not (existing - remaining), "concurrent publication lost or changed existing rows")
    _require(branches[0] != branches[1], "both runs shared one branch")
    _require(expected_total > 0, "neither concurrent run published")
    _require(
        table_count(STRICT_TABLE) - before_rows == expected_total,
        "published total lost existing rows or contains unexpected writes",
    )
    return {
        "scenario": "concurrent_runs",
        "run_ids": launches,
        "classifications": [outcome[0] for outcome in outcomes],
        "rows_added": expected_total,
    }


def run_warning_only(ctx: LabContext) -> dict:
    """Non-blocking violation retains warnings and allows WAP publication."""
    stage_inbound("warning_only")
    before_rows = table_count(RELAXED_TABLE)
    launched_id = materialize(RELAXED_ASSET, "2026-08-24")
    classification, run_id, payload = wait_for_terminal_report(
        launched_id, ctx.reports_dir, ctx.promote_timeout
    )
    after_rows = table_count(RELAXED_TABLE)
    summary = {
        "scenario": "warning_only",
        "classification": classification,
        "failure_reason": (payload or {}).get("failure_reason"),
        "rows_added_to_main": after_rows - before_rows,
        "branch": (payload or {}).get("branch"),
        "run_id": run_id,
    }
    # WARN evidence remains non-blocking while WAP still isolates publication.
    _require(
        classification == "promoted",
        f"warning_only ended {classification}; expected promoted-with-warnings",
    )
    _require(
        summary["failure_reason"] is None,
        f"unexpected failure_reason {summary['failure_reason']}",
    )
    _require(
        after_rows - before_rows == WARNING_ROWS,
        f"main did not advance despite non-blocking checks (+{summary['rows_added_to_main']})",
    )
    return summary


SCENARIOS = {
    "valid_publish": run_valid_publish,
    "quality_failure": run_quality_failure,
    "retry_recovery": run_retry_recovery,
    "schema_change": run_schema_change,
    "concurrent_runs": run_concurrent_runs,
    "warning_only": run_warning_only,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    parser.add_argument("--promote-timeout", type=float, default=900.0)
    parser.add_argument("--failure-timeout", type=float, default=180.0)
    args = parser.parse_args(argv)

    ctx = LabContext(
        reports_dir=args.reports_dir,
        promote_timeout=args.promote_timeout,
        failure_timeout=args.failure_timeout,
    )
    runner = SCENARIOS[args.scenario]
    print(f"=== wap-failure-lab scenario: {args.scenario} ===", flush=True)
    summary = runner(ctx)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"scenario '{args.scenario}' passed all assertions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
