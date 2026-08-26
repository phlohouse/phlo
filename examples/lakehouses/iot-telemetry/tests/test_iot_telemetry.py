"""Fast deterministic contract tests for the IoT telemetry example."""

from __future__ import annotations

import gzip
import json
import sqlite3
from pathlib import Path

import dagster as dg
import pandas as pd
import pytest
from phlo_dlt import get_ingestion_assets

from scripts.generate_fixtures import MAX_FILES_PER_HOUR, generate
from workflows.ingest import devices  # noqa: F401 - import registers reference assets
from workflows.ingest.corrections import read_corrections
from workflows.ingest.readings import read_readings
from workflows.quality.operational import (
    assert_duplicate_ratio_within_threshold,
    assert_file_count_within_threshold,
    assert_registered_devices_only,
    assert_sequence_monotonic,
)
from workflows.schedules import telemetry as telemetry_schedules
from workflows.schemas.telemetry import TelemetryReadingSchema

DAY = "2026-08-20"


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    data = tmp_path_factory.mktemp("fixtures") / "generated-data"
    generate(data)
    return data


def _read_ndjson_gz(path: Path) -> pd.DataFrame:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return pd.DataFrame(rows)


def _registry_devices(data_dir: Path) -> pd.DataFrame:
    connection = sqlite3.connect(data_dir / "device_registry.sqlite")
    try:
        return pd.read_sql_query("SELECT * FROM devices", connection)
    finally:
        connection.close()


def _tree_hash(root: Path) -> list[tuple[str, bytes]]:
    return sorted(
        (str(path.relative_to(root)), path.read_bytes())
        for path in root.rglob("*")
        if path.is_file() and path.suffix != ".sqlite"
    )


def test_fixtures_are_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    summary_one = generate(first)
    summary_two = generate(second)
    assert summary_one == summary_two
    assert _tree_hash(first) == _tree_hash(second)
    connection_a = sqlite3.connect(first / "device_registry.sqlite")
    connection_b = sqlite3.connect(second / "device_registry.sqlite")
    try:
        query = "SELECT * FROM devices ORDER BY device_id"
        assert connection_a.execute(query).fetchall() == connection_b.execute(query).fetchall()
    finally:
        connection_a.close()
        connection_b.close()


def test_raw_batch_passes_contract_and_keeps_labeled_noise(data_dir: Path) -> None:
    readings = read_readings(data_dir / "telemetry")
    validated = TelemetryReadingSchema.validate(readings)
    duplicates = int(validated.message_id.duplicated().sum())
    late = validated[validated.ingested_from_hour > validated.event_hour]
    assert len(validated) == 533
    assert validated.message_id.nunique() == 528
    assert duplicates == 5
    assert duplicates / len(validated) <= 0.02
    assert len(late) == 4
    late_hours = set(late.event_hour.dt.strftime("%Y-%m-%dT%H"))
    assert late_hours == {f"{DAY}T01", f"{DAY}T02", f"{DAY}T03", f"{DAY}T04"}
    assert (late.ingested_from_hour.dt.strftime("%Y-%m-%dT%H") == f"{DAY}T05").all()
    assert_duplicate_ratio_within_threshold(validated)
    assert_sequence_monotonic(validated)
    assert_registered_devices_only(validated, _registry_devices(data_dir))


def test_partition_filter_reads_single_day_only(data_dir: Path) -> None:
    readings = read_readings(data_dir / "telemetry", partition_date=DAY)
    assert set(readings.event_hour.str.slice(0, 10)) == {DAY}
    with pytest.raises(FileNotFoundError, match="2031-01-01"):
        read_readings(data_dir / "telemetry", partition_date="2031-01-01")


def test_corrections_amend_known_messages_by_id(data_dir: Path) -> None:
    corrections = read_corrections(data_dir / "telemetry" / "corrections")
    readings = read_readings(data_dir / "telemetry")
    assert corrections.message_id.isin(readings.message_id).all()
    assert corrections.corrected_temperature_c.notna().sum() == 1
    assert corrections.corrected_humidity_pct.notna().sum() == 1


def test_out_of_bounds_fixture_fails_physical_bounds(data_dir: Path) -> None:
    invalid = _read_ndjson_gz(data_dir / "failures" / "readings_out_of_bounds.ndjson.gz")
    with pytest.raises(Exception, match="temperature_c"):
        TelemetryReadingSchema.validate(invalid)


def test_sequence_regression_fails_monotonicity(data_dir: Path) -> None:
    regression = _read_ndjson_gz(data_dir / "failures" / "readings_sequence_regression.ndjson.gz")
    with pytest.raises(ValueError, match="regress"):
        assert_sequence_monotonic(regression)


def test_unknown_device_fails_registered_device_check(data_dir: Path) -> None:
    unknown = _read_ndjson_gz(data_dir / "failures" / "readings_unknown_device.ndjson.gz")
    with pytest.raises(ValueError, match="dev-999"):
        assert_registered_devices_only(unknown, _registry_devices(data_dir))


def test_duplicate_burst_fails_ratio_threshold(data_dir: Path) -> None:
    burst = _read_ndjson_gz(data_dir / "failures" / "readings_duplicate_burst.ndjson.gz")
    with pytest.raises(ValueError, match="Duplicate ratio"):
        assert_duplicate_ratio_within_threshold(burst)


def test_file_count_pressure_is_detected_and_baseline_passes(data_dir: Path) -> None:
    assert_file_count_within_threshold(data_dir / "telemetry")
    pressure = data_dir / "failures" / "pressure"
    hour_files = list((pressure / f"hour={DAY}T06").glob("*.ndjson.gz"))
    assert len(hour_files) > MAX_FILES_PER_HOUR
    with pytest.raises(ValueError, match=f"{DAY}T06"):
        assert_file_count_within_threshold(pressure)


def test_ingestion_assets_carry_differentiated_contracts() -> None:
    assets = {asset.key: asset for asset in get_ingestion_assets()}
    assert set(assets) == {
        "dlt_telemetry_readings",
        "dlt_telemetry_corrections",
        "dlt_device_registry",
        "dlt_site_directory",
    }
    readings = assets["dlt_telemetry_readings"]
    assert readings.metadata["write_mode"] == "append"
    assert readings.metadata["primary_key"] == ["message_id"]
    assert readings.metadata["owner"] == "fleet-operations"
    assert readings.run.max_retries == 5
    assert readings.run.freshness_hours == (2, 4)
    assert assets["dlt_telemetry_corrections"].metadata["write_mode"] == "merge"
    assert assets["dlt_device_registry"].run.freshness_hours == (168, 192)
    assert all(asset.checks[0].blocking for asset in assets.values())


def test_schedules_cover_ingestion_repair_fleet_and_default_to_stopped() -> None:
    registered = (
        telemetry_schedules.hourly_ingestion_schedule,
        telemetry_schedules.rolling_repair_schedule,
        telemetry_schedules.daily_fleet_schedule,
        telemetry_schedules.weekly_reconciliation_schedule,
    )
    assert {schedule.cron_schedule for schedule in registered} == {
        "20 * * * *",
        "40 * * * *",
        "15 1 * * *",
        "0 3 * * 1",
    }
    assert all(
        schedule.default_status is dg.DefaultScheduleStatus.STOPPED for schedule in registered
    )


def test_dbt_models_implement_dedup_repair_and_publication() -> None:
    root = Path(__file__).resolve().parents[1] / "workflows"

    dedup = (root / "normalize/models/telemetry_dedup.sql").read_text(encoding="utf-8")
    assert "row_number() over (" in dedup and "partition by r.message_id" in dedup
    assert "coalesce(c.corrected_temperature_c, v.temperature_c)" in dedup
    assert "v.ingested_from_hour > v.event_hour as arrived_late" in dedup
    assert "_phlo_ingested_at desc" in dedup

    health = (root / "aggregate/models/device_health_hourly.sql").read_text(encoding="utf-8")
    assert "group by device_id, site_id, event_hour" in health
    fleet = (root / "aggregate/models/fleet_daily_summary.sql").read_text(encoding="utf-8")
    assert "completeness_ratio" in fleet and "active_devices" in fleet

    current = (root / "publish/models/device_health_current.sql").read_text(encoding="utf-8")
    assert "row_number() over (partition by device_id order by event_hour desc)" in current
    report = (root / "publish/models/site_daily_report.sql").read_text(encoding="utf-8")
    assert "ref('stg_sites')" in report

    project = (root / "transforms/dbt/dbt_project.yml").read_text(encoding="utf-8")
    assert '"../../normalize/models"' in project
    assert '"../../aggregate/models"' in project
    assert '"../../publish/models"' in project
    normalize_tests = (root / "normalize/models/schema.yml").read_text(encoding="utf-8")
    assert "phlo_asset_key: dlt_telemetry_readings" in normalize_tests
