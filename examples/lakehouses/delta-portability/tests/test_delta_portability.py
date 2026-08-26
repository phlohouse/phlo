"""Fast deterministic contract tests for the Delta portability example.

Container-free by design. The Delta-backed tests run the real delta-rs engine
against a throwaway local warehouse (``DELTA_WAREHOUSE_PATH``), which is what
makes merge idempotency, schema evolution, time travel, and maintenance
provable rather than asserted from strings.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from pathlib import Path

import dagster as dg
import pandas as pd
import pyarrow as pa
import pytest
import yaml
from phlo_dlt import get_ingestion_assets
from phlo_sling import get_sling_assets

from scripts.generate_fixtures import generate
from scripts.replay_server import serve
from workflows.ingest import (  # noqa: F401 - registers regions asset
    devices,  # noqa: F401 - import registers reference assets
    evolution,
    regions,
)
from workflows.ingest.corrections import read_corrections
from workflows.ingest.readings import read_readings
from workflows.quality.operational import (
    assert_duplicate_ratio_within_threshold,
    assert_event_date_matches_hour,
    assert_registered_devices_only,
    assert_sequence_monotonic,
)
from workflows.schedules import telemetry as telemetry_schedules
from workflows.schemas.telemetry import (
    RegionDirectorySchema,
    TelemetryCorrectionSchema,
    TelemetryReadingSchema,
)
from workflows.sources.postgres import streams  # noqa: F401 - registers sling asset

DAY = "2026-08-20"


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    data = tmp_path_factory.mktemp("fixtures") / "generated-data"
    counts = generate(data)
    assert counts == {
        "reading_rows": 293,
        "reading_messages": 288,
        "late_stragglers": 4,
        "corrections": 2,
        "evolved_rows": 48,
        "regions": 3,
        "devices": 8,
        "sites": 3,
    }
    return data


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Local Delta warehouse; module-scoped so module fixtures inherit the env."""
    path = tmp_path_factory.mktemp("delta-warehouse")
    patch = pytest.MonkeyPatch()
    patch.setenv("DELTA_WAREHOUSE_PATH", str(path))
    yield path
    patch.undo()


# Fixture determinism and contracts


def _tree_hash(root: Path) -> list[tuple[str, bytes]]:
    return sorted(
        (str(path.relative_to(root)), path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    )


def _read_ndjson_gz(path: Path) -> pd.DataFrame:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return pd.DataFrame(rows)


def test_fixtures_are_deterministic(data_dir: Path, tmp_path: Path) -> None:
    second = tmp_path / "second"
    generate(second)
    assert _tree_hash(data_dir) == _tree_hash(second)


def test_raw_batch_passes_contract_and_keeps_labeled_noise(data_dir: Path) -> None:
    readings = read_readings(data_dir / "telemetry")
    validated = TelemetryReadingSchema.validate(readings)

    assert len(validated) == 293
    assert validated.message_id.nunique() == 288
    late = validated[validated.ingested_from_hour > validated.event_hour]
    assert len(late) == 4

    # v1 deliveries never carry the additive column; the optional contract
    # field accepts their absence.
    assert "signal_quality_dbm" not in readings.columns

    assert assert_sequence_monotonic(validated) is None
    assert assert_duplicate_ratio_within_threshold(validated) is None
    assert assert_event_date_matches_hour(validated) is None
    assert assert_registered_devices_only(validated, _registry_devices(data_dir)) is None


def _registry_devices(data_dir: Path) -> pd.DataFrame:
    connection = sqlite3.connect(data_dir / "device_registry.sqlite")
    try:
        return pd.read_sql_query("SELECT * FROM devices", connection)
    finally:
        connection.close()


def test_partition_filter_reads_single_day_only(data_dir: Path) -> None:
    readings = read_readings(data_dir / "telemetry", partition_date=DAY)
    assert set(readings.event_hour.str[:10]) == {DAY}
    with pytest.raises(FileNotFoundError, match="2031-01-01"):
        read_readings(data_dir / "telemetry", partition_date="2031-01-01")


def test_corrections_amend_known_messages_by_id(data_dir: Path) -> None:
    corrections = read_corrections(data_dir / "telemetry" / "corrections")
    validated = TelemetryCorrectionSchema.validate(corrections)

    assert len(validated) == 2
    readings = read_readings(data_dir / "telemetry")
    assert set(validated.message_id).issubset(set(readings.message_id))
    assert corrections.correction_reason.tolist() == ["calibration-offset", "drift-fix"]


def test_regions_replay_validates_against_contract(data_dir: Path) -> None:
    frame = pd.read_csv(data_dir / "regions" / "regions.csv")
    validated = RegionDirectorySchema.validate(frame)
    assert validated.region_code.tolist() == ["north", "south", "east"]
    # The lookup is a superset of the sites' regions; east has no site yet.
    assert {"north", "south"}.issubset(set(validated.region_code))


def test_evolved_batch_passes_optional_column_contract(data_dir: Path) -> None:
    evolved = evolution.read_evolved_batch(data_dir / "evolved" / "readings_v2.csv")
    validated = TelemetryReadingSchema.validate(evolved)

    assert len(validated) == 48
    assert validated.signal_quality_dbm.notna().all()
    assert validated.signal_quality_dbm.between(-120.0, -40.0).all()
    assert set(validated.event_hour.dt.strftime("%Y-%m-%dT%H")) == {f"{DAY}T06"}


# ---------------------------------------------------------------------------
# Labeled failure fixtures: each breaks exactly one invariant


def test_out_of_bounds_fixture_fails_physical_bounds(data_dir: Path) -> None:
    invalid = _read_ndjson_gz(data_dir / "failures" / "readings_out_of_bounds.ndjson.gz")
    with pytest.raises(Exception, match="temperature_c"):
        TelemetryReadingSchema.validate(invalid)


def test_sequence_regression_fails_monotonicity(data_dir: Path) -> None:
    regression = _read_ndjson_gz(data_dir / "failures" / "readings_sequence_regression.ndjson.gz")
    violation = assert_sequence_monotonic(regression)
    assert violation is not None and "regress" in violation


def test_unknown_device_fails_registered_device_check(data_dir: Path) -> None:
    unknown = _read_ndjson_gz(data_dir / "failures" / "readings_unknown_device.ndjson.gz")
    violation = assert_registered_devices_only(unknown, _registry_devices(data_dir))
    assert violation is not None and "dev-999" in violation


def test_duplicate_burst_fails_ratio_threshold(data_dir: Path) -> None:
    burst = _read_ndjson_gz(data_dir / "failures" / "readings_duplicate_burst.ndjson.gz")
    violation = assert_duplicate_ratio_within_threshold(burst)
    assert violation is not None and "duplicate ratio" in violation


def test_signal_out_of_bounds_fixture_fails_additive_bound(data_dir: Path) -> None:
    invalid = pd.read_csv(data_dir / "failures" / "evolved_signal_out_of_bounds.csv")
    with pytest.raises(Exception, match="signal_quality_dbm"):
        TelemetryReadingSchema.validate(invalid)


# ---------------------------------------------------------------------------
# Provider routing: delta table store, no WAP


def _ingestion_assets() -> dict:
    return {asset.key: asset for asset in get_ingestion_assets()}


def test_phlo_yaml_routes_to_delta_without_wap() -> None:
    config = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "phlo.yaml").read_text(encoding="utf-8")
    )
    assert config["capabilities"]["defaults"]["table_store"] == "delta"
    assert config["wap"]["enabled"] is False


def test_ingestion_assets_route_to_delta_with_differentiated_contracts() -> None:
    assets = _ingestion_assets()
    assert set(assets) == {
        "dlt_telemetry_readings",
        "dlt_telemetry_corrections",
        "dlt_device_registry",
        "dlt_site_directory",
        "dlt_delta_regions",
    }
    # Every ingestion asset pins the Delta table store explicitly.
    assert all(asset.capability_overrides == {"table_store": "delta"} for asset in assets.values())

    readings = assets["dlt_telemetry_readings"]
    assert readings.metadata["write_mode"] == "append"
    assert readings.metadata["primary_key"] == ["message_id"]
    assert readings.metadata["owner"] == "fleet-operations"
    assert readings.run.max_retries == 5
    assert readings.run.freshness_hours == (2, 4)

    assert assets["dlt_telemetry_corrections"].metadata["write_mode"] == "merge"
    assert assets["dlt_device_registry"].run.freshness_hours == (168, 192)
    assert all(asset.checks[0].blocking for asset in assets.values())


def test_sling_regions_snapshot_is_full_refresh() -> None:
    sling_assets = {asset.key: asset for asset in get_sling_assets()}
    assert set(sling_assets) == {"sling_delta_regions_snapshot"}
    snapshot = sling_assets["sling_delta_regions_snapshot"]
    assert snapshot.metadata["mode"] == "full-refresh"
    assert snapshot.metadata["primary_key"] == ["region_code"]
    assert snapshot.metadata["table_name"] == "delta_regions_snapshot"


# ---------------------------------------------------------------------------
# Schedules


def test_schedules_cover_four_cadences_and_default_to_stopped() -> None:
    registered = (
        telemetry_schedules.hourly_ingestion_schedule,
        telemetry_schedules.rolling_repair_schedule,
        telemetry_schedules.daily_reference_schedule,
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
    job_names = {schedule.job.name for schedule in registered}
    assert all("wap" not in name for name in job_names)


# ---------------------------------------------------------------------------
# dbt models stay provider-neutral


def test_dbt_models_implement_dedup_repair_region_enrichment() -> None:
    root = Path(__file__).resolve().parents[1] / "workflows"

    dedup = (root / "normalize/models/telemetry_dedup.sql").read_text(encoding="utf-8")
    assert "row_number() over (" in dedup and "partition by r.message_id" in dedup
    assert "coalesce(c.corrected_temperature_c, v.temperature_c)" in dedup
    assert "v.ingested_from_hour > v.event_hour as arrived_late" in dedup
    assert "_phlo_ingested_at desc" in dedup
    assert "signal_quality_dbm" in dedup

    stg_regions = (root / "normalize/models/stg_regions.sql").read_text(encoding="utf-8")
    assert "source('delta_raw', 'delta_regions')" in stg_regions

    health = (root / "aggregate/models/device_health_hourly.sql").read_text(encoding="utf-8")
    assert "group by device_id, site_id, event_hour" in health
    fleet = (root / "aggregate/models/fleet_daily_summary.sql").read_text(encoding="utf-8")
    assert "completeness_ratio" in fleet and "active_devices" in fleet

    report = (root / "publish/models/site_daily_report.sql").read_text(encoding="utf-8")
    assert "ref('stg_sites')" in report and "ref('stg_regions')" in report
    current = (root / "publish/models/device_health_current.sql").read_text(encoding="utf-8")
    assert "row_number() over (partition by device_id order by event_hour desc)" in current

    project = (root / "transforms/dbt/dbt_project.yml").read_text(encoding="utf-8")
    assert '"../../normalize/models"' in project
    assert '"../../aggregate/models"' in project
    assert '"../../publish/models"' in project
    sources = (root / "normalize/models/schema.yml").read_text(encoding="utf-8")
    assert "phlo_asset_key: dlt_telemetry_readings" in sources
    assert "phlo_asset_key: dlt_delta_regions" in sources
    profiles = (root / "transforms/dbt/profiles/profiles.yml").read_text(encoding="utf-8")
    # #783 shipped an optional Delta catalog in the dev-stack Trino; the
    # profile points at it so models read exactly what ingestion writes.
    assert "catalog: delta" in profiles


# ---------------------------------------------------------------------------
# Real Delta behaviour against a local warehouse


READING_ARROW_FIELDS = [
    ("message_id", pa.string()),
    ("device_id", pa.string()),
    ("site_id", pa.string()),
    ("sequence_number", pa.int64()),
    ("event_time", pa.timestamp("us", tz="UTC")),
    ("event_hour", pa.timestamp("us", tz="UTC")),
    ("ingested_from_hour", pa.timestamp("us", tz="UTC")),
    ("temperature_c", pa.float64()),
    ("humidity_pct", pa.float64()),
    ("battery_pct", pa.float64()),
    ("firmware", pa.string()),
    ("rssi_dbm", pa.int64()),
    ("signal_quality_dbm", pa.float64()),
    ("event_date", pa.string()),
]

CORRECTION_ARROW_FIELDS = [
    ("message_id", pa.string()),
    ("corrected_temperature_c", pa.float64()),
    ("corrected_humidity_pct", pa.float64()),
    ("correction_reason", pa.string()),
    ("corrected_at", pa.timestamp("us", tz="UTC")),
]


def _frame_to_arrow(frame: pd.DataFrame, fields: list[tuple[str, pa.DataType]]) -> pa.Table:
    columns: dict[str, pa.Array] = {}
    for name, arrow_type in fields:
        if name not in frame.columns:
            # Optional contract columns may be absent from a delivery; the
            # table still carries them, so fill them with nulls.
            columns[name] = pa.array([None] * len(frame), type=arrow_type)
            continue
        series = frame[name]
        if pa.types.is_timestamp(arrow_type):
            values = pd.to_datetime(series, utc=True)
            array = pa.array(values, type=arrow_type)
        elif series.dtype == object and arrow_type != pa.string():
            array = pa.array(series.astype(float).where(series.notna(), None), type=arrow_type)
        else:
            array = pa.array(series, type=arrow_type)
        columns[name] = array
    schema = pa.schema([pa.field(name, typ, nullable=True) for name, typ in fields])
    return pa.table(columns, schema=schema)


@pytest.fixture(scope="module")
def delta_state(data_dir: Path, warehouse: Path):
    """Run the full append/merge lifecycle once and record observed state."""
    from phlo_delta.resource import DeltaResource

    store = DeltaResource()
    reading_schema = pa.schema(
        [pa.field(name, typ, nullable=True) for name, typ in READING_ARROW_FIELDS]
    )
    correction_schema = pa.schema(
        [pa.field(name, typ, nullable=True) for name, typ in CORRECTION_ARROW_FIELDS]
    )

    readings_frame = TelemetryReadingSchema.validate(read_readings(data_dir / "telemetry"))
    readings_table = _frame_to_arrow(readings_frame, READING_ARROW_FIELDS)

    store.ensure_table("raw.telemetry_readings", reading_schema, [("event_date", "identity")])
    staged = data_dir / "staging-readings.parquet"
    pa.parquet.write_table(readings_table, staged)
    append_result = store.append_parquet("raw.telemetry_readings", str(staged))
    version_after_append = store.get_table("raw.telemetry_readings").version()

    corrections_frame = TelemetryCorrectionSchema.validate(
        read_corrections(data_dir / "telemetry" / "corrections")
    )
    corrections_table = _frame_to_arrow(corrections_frame, CORRECTION_ARROW_FIELDS)

    store.ensure_table("raw.telemetry_corrections", correction_schema)
    first = data_dir / "staging-corrections.parquet"
    pa.parquet.write_table(corrections_table, first)
    merge_one = store.merge_parquet(
        "raw.telemetry_corrections", str(first), unique_key="message_id"
    )

    replay = data_dir / "staging-corrections-replay.parquet"
    pa.parquet.write_table(corrections_table, replay)
    merge_two = store.merge_parquet(
        "raw.telemetry_corrections", str(replay), unique_key="message_id"
    )

    good_version = store.get_table("raw.telemetry_corrections").version()

    # A bad delivery lands without branch isolation: it goes straight to main.
    bad_row = corrections_table.slice(0, 1)
    bad = data_dir / "staging-bad.parquet"
    pa.parquet.write_table(bad_row, bad)
    store.merge_parquet("raw.telemetry_corrections", str(bad), unique_key="message_id")

    rollback_result = store.rollback_to_snapshot(
        table_name="raw.telemetry_corrections", snapshot_id=good_version
    )

    compaction = store.compact(table_name="raw.telemetry_readings")
    vacuum = store.vacuum(table_name="raw.telemetry_readings", retain_hours=168)

    return {
        "store": store,
        "readings_rows": append_result["rows_inserted"],
        "version_after_append": version_after_append,
        "merge_one": merge_one,
        "merge_two": merge_two,
        "good_version": good_version,
        "rollback_result": rollback_result,
        "compaction": compaction,
        "vacuum": vacuum,
    }


def test_append_loads_every_delivery(delta_state) -> None:
    assert delta_state["readings_rows"] == 293


def test_merge_is_idempotent_across_replays(delta_state) -> None:
    one, two = delta_state["merge_one"], delta_state["merge_two"]
    # First replay inserts the amendments; replaying them updates in place.
    assert one["rows_inserted"] == 2 and one["rows_updated"] == 0
    assert two["rows_inserted"] == 0 and two["rows_updated"] == 2
    table = delta_state["store"].get_table("raw.telemetry_corrections")
    assert pa.table(table.to_pyarrow_dataset().to_table()).num_rows == 2


def test_bad_delivery_recovers_via_time_travel_restore(delta_state) -> None:
    store = delta_state["store"]
    restore = delta_state["rollback_result"]

    # The bad row merged straight onto main (no branch isolation); restoring
    # the last known-good version removes it - Delta's substitute for the
    # WAP discard path.
    assert restore["rolled_back_to"] == delta_state["good_version"]
    recovered = store.get_table("raw.telemetry_corrections")
    assert pa.table(recovered.to_pyarrow_dataset().to_table()).num_rows == 2


def test_history_lists_versions_and_maintenance_reports_are_wellformed(
    delta_state,
) -> None:
    store = delta_state["store"]
    history = store.list_snapshots(table_name="raw.telemetry_readings", limit=10)
    operations = [entry.get("operation") for entry in history]
    assert "WRITE" in operations
    assert history[0]["version"] >= delta_state["version_after_append"]

    assert delta_state["compaction"]["compaction"]["numFilesAdded"] >= 0
    assert delta_state["vacuum"]["files_removed"] >= 0


def test_history_script_reports_tables(warehouse: Path, capsys) -> None:
    from scripts.delta_history import main as history_main

    reports = history_main(["raw.telemetry_corrections"], limit=5)
    captured = capsys.readouterr()
    assert "raw.telemetry_corrections" in captured.out
    assert reports[0]["table"] == "raw.telemetry_corrections"
    assert reports[0]["versions"]


def test_schema_evolution_adds_optional_column_in_place(data_dir: Path, warehouse: Path) -> None:
    """v1 table gains signal_quality_dbm via plan + non-destructive add."""
    import pyarrow.parquet as pq
    from phlo_delta.resource import DeltaResource

    store = DeltaResource()
    v1_fields = [field for field in READING_ARROW_FIELDS if field[0] != "signal_quality_dbm"]
    v1_schema = pa.schema([pa.field(name, typ, nullable=True) for name, typ in v1_fields])

    readings = TelemetryReadingSchema.validate(read_readings(data_dir / "telemetry"))
    store.ensure_table("raw.telemetry_readings_v1", v1_schema, [("event_date", "identity")])
    v1_batch = data_dir / "staging-v1.parquet"
    pq.write_table(_frame_to_arrow(readings, v1_fields), v1_batch)
    store.append_parquet("raw.telemetry_readings_v1", str(v1_batch))

    plan = evolution.plan_signal_quality_addition("raw.telemetry_readings_v1")
    assert [(change.change_type, change.field_name) for change in plan.changes] == [
        ("add", "signal_quality_dbm")
    ]
    assert plan.classification == "safe"

    applied = evolution.apply_plan_additive("raw.telemetry_readings_v1", plan)
    assert applied == {"added_columns": ["signal_quality_dbm"]}

    evolved = evolution.read_evolved_batch(data_dir / "evolved" / "readings_v2.csv")
    evolved_table = _frame_to_arrow(evolved, READING_ARROW_FIELDS)
    evolved_batch = data_dir / "staging-evolved.parquet"
    pq.write_table(evolved_table, evolved_batch)
    result = store.append_parquet("raw.telemetry_readings_v1", str(evolved_batch))
    assert result["rows_inserted"] == 48

    dataset = store.get_table("raw.telemetry_readings_v1").to_pyarrow_dataset()
    combined = pa.table(dataset.to_table())
    assert combined.num_rows == 293 + 48
    frame = combined.to_pandas()
    legacy_ids = set(readings.message_id)
    legacy = frame[frame.message_id.isin(legacy_ids)]
    refreshed = frame[~frame.message_id.isin(legacy_ids)]
    assert len(legacy) == 293 and legacy.signal_quality_dbm.isna().all()
    assert len(refreshed) == 48 and refreshed.signal_quality_dbm.notna().all()


def test_rest_replay_server_serves_the_evolved_batch(data_dir: Path) -> None:
    server = serve(data_dir=data_dir, port=0)
    port = server.server_address[1]
    try:
        batch = evolution.fetch_evolved_batch(f"http://127.0.0.1:{port}/v1")
    finally:
        server.shutdown()
    assert len(batch) == 48
    assert set(batch.columns) >= {"message_id", "event_hour", "signal_quality_dbm"}
