"""Fast deterministic tests for the WAP failure lab.

Container-free: fixtures are generated into temp dirs, validators run on plain
DataFrames, WAP report parsing runs against synthetic payloads copied from the
``phlo.wap_report.v2`` schema, and retry/concurrency logic is exercised as
pure functions.
"""

from __future__ import annotations

import gzip
import json
import sqlite3  # noqa: F401 - kept for parity with sibling suites' registry reads
from pathlib import Path

import dagster as dg
import pandas as pd
import pytest
from phlo_dlt import get_ingestion_assets

import workflows.ingest.batches  # noqa: F401 - import registers ingestion assets
from scripts.generate_fixtures import (
    CONCURRENT_A_ROWS,
    CONCURRENT_B_ROWS,
    PARTITION_A,
    RETRY_PARTITION,
    SCHEMA_PARTITION,
    VALID_ROWS,
    WARNING_PARTITION,
    build_concurrent_partition_a,
    build_concurrent_partition_b,
    generate,
)
from scripts.inspect_branches import inspect_branches  # noqa: F401 - import guard
from scripts.run_scenario import (
    REPORT_SCHEMA_VERSION,
    SCENARIOS,
    classify_report,
    list_reports,
    load_report,
    report_ids_for_branch,
)
from workflows.quality.validators import (
    STALENESS_MAX_DAYS,
    assert_batch_ids_unique,
    assert_recordings_near_partition,
)
from workflows.retry.transient import TransientSourceError, raise_if_first_attempt, read_attempts
from workflows.schedules import lab as lab_schedules
from workflows.schemas.contracts import BASE_COLUMNS, SensorBatchSchema

PROMOTED_REPORT = {
    "schema_version": REPORT_SCHEMA_VERSION,
    "run_id": "0a4b3f2d1c9e8f7a6b5c4d3e2f1a0b9c",
    "dagster_run_id": "6f1e2d3c4b5a69788796a5b4c3d2e1f0",
    "status": "promoted",
    "branch": "pipeline-run-0a4b3f2d1c9e8f7a6b5c4d3e2f1a0b9c",
    "target_branch": "main",
    "source_hash": "aa" * 16,
    "target_hash_before": "bb" * 16,
    "target_hash_after": "cc" * 16,
    "launch_tags": {
        "phlo/run_id": "0a4b3f2d1c9e8f7a6b5c4d3e2f1a0b9c",
        "phlo/wap_branch": "pipeline-run-0a4b3f2d1c9e8f7a6b5c4d3e2f1a0b9c",
        "phlo/ref": "pipeline-run-0a4b3f2d1c9e8f7a6b5c4d3e2f1a0b9c",
    },
}

BLOCKED_REPORT = {
    **PROMOTED_REPORT,
    "status": "promotion_blocked",
    "failure_reason": "asset_checks_failed",
}

LAUNCHED_REPORT = {
    **PROMOTED_REPORT,
    "status": "launched",
    "target_hash_after": None,
}

FAILED_RUN_REPORT = {
    **LAUNCHED_REPORT,
    "status": "failed",
    "failure_reason": "dagster_run_failed",
}


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    data = tmp_path_factory.mktemp("fixtures") / "generated-data"
    generate(data)
    return data


def _read_ndjson_gz(path: Path) -> pd.DataFrame:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return pd.DataFrame(rows)


def _scenario_file(data_dir: Path, scenario: str, filename: str) -> pd.DataFrame:
    return _read_ndjson_gz(data_dir / "scenarios" / scenario / filename)


# ---------------------------------------------------------------------------
# Fixtures


def test_fixtures_are_byte_stable(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate(first)
    generate(second)

    def tree_hash(root: Path) -> dict[str, bytes]:
        return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*.gz"))}

    assert tree_hash(first) == tree_hash(second)


def test_inbound_defaults_to_valid_publish(data_dir: Path) -> None:
    inbound = sorted(p.name for p in (data_dir / "inbound").glob("*.ndjson.gz"))
    assert inbound == [f"batches-{PARTITION_A}.ndjson.gz"]


def test_valid_publish_fixture_passes_contract_and_validators(data_dir: Path) -> None:
    frame = _scenario_file(data_dir, "valid_publish", f"batches-{PARTITION_A}.ndjson.gz")
    validated = SensorBatchSchema.validate(frame)
    assert len(validated) == VALID_ROWS
    assert assert_batch_ids_unique(validated) is None
    assert assert_recordings_near_partition(validated) is None


def test_null_reading_fixture_breaks_only_the_contract(data_dir: Path) -> None:
    nulls = _scenario_file(
        data_dir, "quality_failure", f"batches_null_reading-{PARTITION_A}.ndjson.gz"
    )
    with pytest.raises(Exception, match="reading_value"):
        SensorBatchSchema.validate(nulls)
    # The domain checks stay green: exactly one invariant is broken per fixture.
    assert assert_batch_ids_unique(nulls) is None
    assert assert_recordings_near_partition(nulls) is None


def test_duplicate_fixture_breaks_only_uniqueness(data_dir: Path) -> None:
    duplicates = _scenario_file(
        data_dir, "quality_failure", f"batches_duplicate_batch_id-{PARTITION_A}.ndjson.gz"
    )
    validated = SensorBatchSchema.validate(duplicates)
    violation = assert_batch_ids_unique(validated)
    assert violation is not None and "b-2003" in violation
    assert assert_recordings_near_partition(validated) is None


def test_stale_fixture_breaks_only_the_staleness_window(data_dir: Path) -> None:
    stale = _scenario_file(data_dir, "warning_only", f"batches_stale-{WARNING_PARTITION}.ndjson.gz")
    validated = SensorBatchSchema.validate(stale)
    violation = assert_recordings_near_partition(validated)
    assert violation is not None and f"{STALENESS_MAX_DAYS}-day" in violation
    assert assert_batch_ids_unique(validated) is None


# ---------------------------------------------------------------------------
# Schema change


def test_schema_change_is_additive_and_back_compatible(data_dir: Path) -> None:
    changed = _scenario_file(data_dir, "schema_change", f"batches-{SCHEMA_PARTITION}.ndjson.gz")
    validated = SensorBatchSchema.validate(changed)
    assert len(validated) == 8
    assert validated.reading_quality_score.notna().all()

    # Old readers: pre-change batches lack the column entirely yet validate.
    legacy = _scenario_file(data_dir, "valid_publish", f"batches-{PARTITION_A}.ndjson.gz")
    assert "reading_quality_score" not in legacy.columns
    SensorBatchSchema.validate(legacy)

    # Exactly one column was added to the declared physical shape.
    assert len(SensorBatchSchema.to_schema().columns) == len(BASE_COLUMNS) + 1


# ---------------------------------------------------------------------------
# Retry logic


def test_transient_failure_fires_once_then_recovers(tmp_path: Path) -> None:
    counter = tmp_path / "attempts.txt"
    with pytest.raises(TransientSourceError, match="attempt 1"):
        raise_if_first_attempt(counter_path=counter, armed=True)
    attempt = raise_if_first_attempt(counter_path=counter, armed=True)
    assert attempt == 2
    assert read_attempts(counter) == 2


def test_unarmed_runs_record_a_single_attempt(tmp_path: Path) -> None:
    counter = tmp_path / "attempts.txt"
    assert raise_if_first_attempt(counter_path=counter, armed=False) == 1
    assert read_attempts(counter) == 1


# ---------------------------------------------------------------------------
# Concurrent partition independence


def test_concurrent_partitions_are_independent() -> None:
    partition_a = pd.DataFrame(build_concurrent_partition_a())
    partition_b = pd.DataFrame(build_concurrent_partition_b())

    assert set(partition_a.batch_id).isdisjoint(partition_b.batch_id)
    assert set(partition_a.sensor_id).isdisjoint(partition_b.sensor_id)

    counts_a = partition_a.groupby("sensor_id").size()
    counts_b = partition_b.groupby("sensor_id").size()
    assert counts_a.tolist() == [3, 3, 3, 3]
    assert counts_b.tolist() == [2, 2, 2, 2]
    assert len(partition_a) == CONCURRENT_A_ROWS and len(partition_b) == CONCURRENT_B_ROWS


# ---------------------------------------------------------------------------
# WAP report parsing against synthetic phlo.wap_report.v2 payloads


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (PROMOTED_REPORT, "promoted"),
        (BLOCKED_REPORT, "blocked"),
        (LAUNCHED_REPORT, "in_flight"),
        (FAILED_RUN_REPORT, "failed"),
        (None, "missing"),
    ],
)
def test_report_classification(payload: dict | None, expected: str) -> None:
    assert classify_report(payload) == expected


def test_report_payload_carries_v2_schema_and_promotion_hashes() -> None:
    assert PROMOTED_REPORT["schema_version"] == "phlo.wap_report.v2"
    assert PROMOTED_REPORT["branch"].startswith("pipeline-run-")


def test_report_listing_and_branch_lookup(tmp_path: Path) -> None:
    other_branch = {**BLOCKED_REPORT, "branch": "pipeline-run-blocked"}
    for name, payload in (("run-a", PROMOTED_REPORT), ("run-b", other_branch)):
        (tmp_path / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    reports = list_reports(tmp_path)
    assert set(reports) == {"run-a", "run-b"}
    assert report_ids_for_branch(PROMOTED_REPORT["branch"], tmp_path) == ["run-a"]
    assert report_ids_for_branch("pipeline-run-blocked", tmp_path) == ["run-b"]
    assert load_report(tmp_path / "run-a.json")["status"] == "promoted"


# ---------------------------------------------------------------------------
# Registered assets, schedules, dbt evidence, scenarios


def test_ingestion_assets_carry_differentiated_blocking_semantics() -> None:
    assets = {asset.key: asset for asset in get_ingestion_assets()}
    assert set(assets) == {"dlt_sensor_batches", "dlt_sensor_batches_relaxed"}

    strict = assets["dlt_sensor_batches"]
    relaxed = assets["dlt_sensor_batches_relaxed"]

    assert all(check.blocking for check in strict.checks)
    assert all(not check.blocking for check in relaxed.checks)
    assert strict.run.max_retries == 3
    assert relaxed.run.max_retries == 1
    assert strict.metadata["write_mode"] == "append"


def test_schedules_are_distinct_and_stopped() -> None:
    registered = (
        lab_schedules.hourly_relaxed_feed_schedule,
        lab_schedules.daily_batch_ingestion_schedule,
        lab_schedules.weekly_wap_reconciliation_schedule,
    )
    assert {schedule.cron_schedule for schedule in registered} == {
        "10 * * * *",
        "30 2 * * *",
        "0 4 * * 1",
    }
    assert all(
        schedule.default_status is dg.DefaultScheduleStatus.STOPPED for schedule in registered
    )
    assert lab_schedules.weekly_wap_reconciliation_job.name == "wap_failure_lab_wap_job"


def test_dbt_model_aggregates_only_published_rows() -> None:
    root = Path(__file__).resolve().parents[1] / "workflows"

    summary = (root / "transforms/dbt/models/batch_summary.sql").read_text(encoding="utf-8")
    assert "source('raw', 'sensor_batches')" in summary
    assert "group by sensor_id" in summary
    assert "count(distinct batch_id)" in summary

    sources = (root / "transforms/dbt/models/schema.yml").read_text(encoding="utf-8")
    assert "phlo_asset_key: dlt_sensor_batches" in sources

    project = (root / "transforms/dbt/dbt_project.yml").read_text(encoding="utf-8")
    assert 'model-paths: ["models"]' in project

    profiles = (root / "transforms/dbt/profiles/profiles.yml").read_text(encoding="utf-8")
    assert "type: trino" in profiles and "catalog: iceberg" in profiles


def test_retry_recovery_batch_matches_declared_partition(data_dir: Path) -> None:
    retry = _scenario_file(data_dir, "retry_recovery", f"batches-{RETRY_PARTITION}.ndjson.gz")
    assert len(retry) == 10
    assert set(pd.to_datetime(retry.batch_date).dt.strftime("%Y-%m-%d")) == {RETRY_PARTITION}


def test_scenario_registry_matches_scenario_directories() -> None:
    scenarios_root = Path(__file__).resolve().parents[1] / "scenarios"
    on_disk = {p.name for p in scenarios_root.iterdir() if p.is_dir()}
    assert set(SCENARIOS) == on_disk
    for name in SCENARIOS:
        assert (scenarios_root / name / "SCENARIO.md").is_file(), name
