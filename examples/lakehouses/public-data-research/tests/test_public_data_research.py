"""Fast deterministic contract tests for the public-data research example."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import dagster as dg
import pandas as pd
import pandera.errors as pa_errors
import pytest
from phlo_dlt import get_ingestion_assets

from scripts.civic_api import serve
from scripts.generate_fixtures import (
    DRIFT_MONTH,
    F_TO_C,
    REGION_DEMOGRAPHICS,
    REGISTRY_BASELINE_DATE,
    REGISTRY_REVISION_DATE,
    REVISED_PLACE,
    WEATHER_MONTHS,
    build_registry_payloads,
    generate,
    observation_rows,
)
from workflows.research import schedules as research_schedules
from workflows.research.indicators.reconciliation import (
    monthly_indicators,
    normalize_observations,
    rollup_reconciliation,
    to_celsius,
)
from workflows.schemas.contracts import (
    ObservationSchema,
    PlaceRecordSchema,
    PlacesGeoSchema,
    RegionDemographicsSchema,
)
from workflows.sources.civic_api.geo import parse_places_geojson
from workflows.sources.civic_api.registry import fetch_places
from workflows.sources.demographics.population import read_demographics_year
from workflows.sources.weather_files.observations import read_month_archive
from workflows.sources.weather_files.quality import assert_known_stations


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    data = tmp_path_factory.mktemp("fixtures") / "generated-data"
    generate(data)
    return data


def _tree_hash(root: Path) -> list[tuple[str, bytes]]:
    return sorted(
        (str(path.relative_to(root)), path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    )


def _read_archive_frame(data_dir: Path, month: str) -> pd.DataFrame:
    frame = read_month_archive(month, data_dir / "weather")
    frame["obs_month"] = pd.Timestamp(f"{month}-01")
    return frame


def _observation_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Build a contract-ready frame: parsed timestamps plus the merge surrogate."""
    frame = pd.DataFrame(rows)
    frame["observed_at"] = pd.to_datetime(frame["observed_at"])
    frame["observation_key"] = frame["station_id"] + "|" + frame["observed_at"].astype(str)
    frame["obs_month"] = frame["observed_at"].dt.to_period("M").dt.start_time
    return frame


# ---------------------------------------------------------------------------
# Fixture determinism
# ---------------------------------------------------------------------------


def test_fixtures_are_deterministic_including_zip_bytes(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    summary_one = generate(first)
    summary_two = generate(second)
    assert (
        summary_one
        == summary_two
        == {
            "places": 5,
            "registry_dates": 2,
            "observations": 72,
            "weather_archives": 3,
            "demographics_rows": 4,
            "failures": 2,
        }
    )
    assert _tree_hash(first) == _tree_hash(second)

    with zipfile.ZipFile(first / "weather" / f"weather-{DRIFT_MONTH}.zip") as archive_one:
        drift_bytes_one = {name: archive_one.read(name) for name in archive_one.namelist()}
    with zipfile.ZipFile(second / "weather" / f"weather-{DRIFT_MONTH}.zip") as archive_two:
        drift_bytes_two = {name: archive_two.read(name) for name in archive_two.namelist()}
    assert drift_bytes_one == drift_bytes_two


def test_drift_month_carries_pressure_column_and_earlier_months_do_not(data_dir: Path) -> None:
    for month in WEATHER_MONTHS:
        if month == DRIFT_MONTH:
            continue
        frame = _read_archive_frame(data_dir, month)
        assert set(frame.columns).isdisjoint({"pressure_hpa"})
    drift = _read_archive_frame(data_dir, DRIFT_MONTH)
    assert set(drift.columns).issuperset({"pressure_hpa"})
    assert drift["pressure_hpa"].between(1012.0, 1015.5).all()


# ---------------------------------------------------------------------------
# Replay API + registry revision
# ---------------------------------------------------------------------------


def test_replay_server_serves_paginated_registry_and_revision(data_dir: Path) -> None:
    server = serve(data_dir=data_dir, port=0)
    try:
        host, port = server.server_address[:2]
        base_url = f"http://{host}:{port}/v1"

        baseline = fetch_places(REGISTRY_BASELINE_DATE, url=base_url)
        assert len(baseline) == 5
        assert {row["place_id"] for row in baseline} == {"P1", "P2", "P3", "P4", "P5"}

        revision = fetch_places(REGISTRY_REVISION_DATE, url=base_url)
        assert [row["place_id"] for row in revision] == [REVISED_PLACE]
    finally:
        server.shutdown()


def test_upstream_revision_changes_exactly_one_field() -> None:
    baseline_rows = build_registry_payloads()[REGISTRY_BASELINE_DATE]["pages"]
    revised_row = build_registry_payloads()[REGISTRY_REVISION_DATE]["pages"][0][0]
    original = next(
        row for page in baseline_rows for row in page if row["place_id"] == REVISED_PLACE
    )
    differences = {field for field in original if original[field] != revised_row.get(field)}
    assert differences == {"population"}
    assert revised_row["population"] > original["population"]


def test_baseline_frames_pass_contracts(data_dir: Path) -> None:
    baseline_rows = [
        row for page in build_registry_payloads()[REGISTRY_BASELINE_DATE]["pages"] for row in page
    ]
    registry = pd.DataFrame(baseline_rows)
    registry["registry_date"] = pd.Timestamp(REGISTRY_BASELINE_DATE)
    PlaceRecordSchema.validate(registry)

    geo = parse_places_geojson(data_dir / "civic" / "places.geojson")
    PlacesGeoSchema.validate(geo)

    demographics = pd.concat(
        [read_demographics_year(str(year), data_dir / "demographics") for year in (2025, 2026)],
        ignore_index=True,
    )
    RegionDemographicsSchema.validate(demographics)


# ---------------------------------------------------------------------------
# Schema drift vs malformed batches
# ---------------------------------------------------------------------------


def test_drift_batch_validates_under_the_contract() -> None:
    drift_frame = _observation_frame(observation_rows(DRIFT_MONTH))
    ObservationSchema.validate(drift_frame)

    pre_drift_frame = _observation_frame(observation_rows("2026-05"))
    ObservationSchema.validate(pre_drift_frame)


def test_malformed_precipitation_breaks_exactly_the_numeric_contract(
    data_dir: Path,
) -> None:
    malformed = pd.read_csv(data_dir / "failures" / "precip_negative.csv")
    malformed["observed_at"] = pd.to_datetime(malformed["observed_at"])
    assert len(malformed) == 1
    with pytest.raises(pa_errors.SchemaError, match="precip_mm"):
        ObservationSchema.validate(malformed)


# ---------------------------------------------------------------------------
# Coverage gate
# ---------------------------------------------------------------------------


def test_known_stations_pass_coverage_gate() -> None:
    observations = pd.DataFrame(observation_rows("2026-06"))
    assert assert_known_stations(observations) is None
    empty = pd.DataFrame(columns=["station_id", "observed_at"])
    assert assert_known_stations(empty) is None


def test_orphan_station_fixture_breaks_only_the_coverage_invariant(data_dir: Path) -> None:
    orphans = pd.read_csv(data_dir / "failures" / "observations_orphan_station.csv")
    orphans["observed_at"] = pd.to_datetime(orphans["observed_at"])
    orphans["observation_key"] = orphans["station_id"] + "|" + orphans["observed_at"].astype(str)
    orphans["obs_month"] = pd.to_datetime(orphans["observed_at"]).dt.to_period("M").dt.start_time
    # The contract itself accepts the rows; only coverage rejects them.
    ObservationSchema.validate(orphans)
    violation = assert_known_stations(orphans)
    assert violation is not None
    assert "PX" in violation
    assert "unknown stations" in violation


# ---------------------------------------------------------------------------
# Unit conversion and indicator arithmetic
# ---------------------------------------------------------------------------


def test_unit_conversion_is_exact_for_every_fixture_pair() -> None:
    for temp_f, temp_c in F_TO_C:
        assert to_celsius(temp_f, True) == pytest.approx(temp_c)
        assert to_celsius(temp_c, False) == temp_c

    rows = observation_rows("2026-06")
    staged = normalize_observations(pd.DataFrame(rows))
    flagged = staged[staged["unit_f"]]
    expected = {to_celsius(temp_f, True) for temp_f, _ in F_TO_C}
    assert set(flagged["temp_c"]) <= expected
    unflagged = staged[~staged["unit_f"]]
    raw_unflagged = pd.DataFrame(rows)[~pd.DataFrame(rows)["unit_f"]]["temp_c"]
    assert (unflagged["temp_c"].to_numpy() == raw_unflagged.to_numpy()).all()


def test_monthly_indicators_follow_from_fixture_arithmetic() -> None:
    rows = observation_rows("2026-06")
    indicators = monthly_indicators(pd.DataFrame(rows))
    assert len(indicators) == 4  # four stations, one month each
    assert int(indicators["observation_count"].sum()) == len(rows)  # 24 rows


def test_rollup_reconciliation_holds_for_every_station_year() -> None:
    all_months = pd.concat(
        [pd.DataFrame(observation_rows(month)) for month in WEATHER_MONTHS],
        ignore_index=True,
    )
    reconciled = rollup_reconciliation(all_months)
    assert len(reconciled) == 4  # one station-year row per station
    assert (reconciled["census_year"] == pd.Timestamp("2026-01-01")).all()
    assert (reconciled["precip_delta"] == 0).all()

    demographics_expected = {region for (region, year) in REGION_DEMOGRAPHICS if year == 2026}
    assert demographics_expected == {"north", "south"}


# ---------------------------------------------------------------------------
# Registered ingestion assets
# ---------------------------------------------------------------------------


def test_ingestion_assets_carry_differentiated_contracts() -> None:
    import workflows.sources.civic_api.geo  # noqa: F401 - registration side effect
    import workflows.sources.civic_api.registry  # noqa: F401 - registration side effect
    import workflows.sources.demographics.population  # noqa: F401 - registration side effect
    import workflows.sources.weather_files.observations  # noqa: F401 - registration side effect

    assets = {asset.key: asset for asset in get_ingestion_assets()}
    assert set(assets) == {
        "dlt_places_registry",
        "dlt_places_geo",
        "dlt_weather_observations",
        "dlt_region_demographics",
    }

    registry = assets["dlt_places_registry"]
    assert registry.metadata["write_mode"] == "merge"
    assert registry.metadata["primary_key"] == ["place_id"]
    assert registry.metadata["owner"] == "civic-platform"
    assert registry.metadata["group"] == "civic_api"
    assert registry.partitions is not None
    assert registry.run.freshness_hours == (26, 30)
    assert registry.run.max_retries == 3

    geo = assets["dlt_places_geo"]
    assert geo.metadata["write_mode"] == "merge"
    assert geo.metadata["primary_key"] == ["place_id"]
    assert geo.partitions is None  # partitioned=False reference-style merge
    assert geo.run.freshness_hours == (168, 192)

    weather = assets["dlt_weather_observations"]
    assert weather.metadata["write_mode"] == "merge"
    assert weather.metadata["primary_key"] == ["observation_key"]
    assert weather.partitions is not None
    assert weather.run.freshness_hours == (744, 800)
    assert any(check.blocking for check in weather.checks)

    demographics = assets["dlt_region_demographics"]
    assert demographics.metadata["primary_key"] == ["region_year"]
    assert demographics.run.freshness_hours == (8760, 8800)
    assert demographics.partitions is not None


def test_partition_specs_declare_mixed_temporal_grains() -> None:
    import workflows.sources.civic_api.geo  # noqa: F401 - registration side effect
    import workflows.sources.civic_api.registry  # noqa: F401 - registration side effect
    import workflows.sources.demographics.population  # noqa: F401 - registration side effect
    import workflows.sources.weather_files.observations  # noqa: F401 - registration side effect
    from workflows.sources.civic_api.geo import places_geo
    from workflows.sources.civic_api.registry import places_registry
    from workflows.sources.demographics.population import region_demographics
    from workflows.sources.weather_files.observations import weather_observations

    specs = {
        func.__name__: func._phlo_table_config.partition_spec  # type: ignore[attr-defined]
        for func in (places_geo, places_registry, weather_observations, region_demographics)
    }
    assert specs["places_registry"] == [("registry_date", "identity")]
    assert specs["weather_observations"] == [("obs_month", "identity")]
    assert specs["region_demographics"] == [("census_year", "identity")]
    assert specs["places_geo"] is None  # reference merge ignores partitions


# ---------------------------------------------------------------------------
# Schedules and dbt evidence
# ---------------------------------------------------------------------------


def test_schedules_have_distinct_cadences_and_default_to_stopped() -> None:
    schedules = (
        research_schedules.civic_daily_ingestion_schedule,
        research_schedules.weather_monthly_ingestion_schedule,
        research_schedules.demographics_annual_ingestion_schedule,
        research_schedules.research_rebuild_schedule,
        research_schedules.weekly_reconciliation_schedule,
    )
    assert {schedule.cron_schedule for schedule in schedules} == {
        "15 6 * * *",
        "0 7 2 * *",
        "0 8 1 2 *",
        "45 7 * * *",
        "0 6 * * 6",
    }
    assert all(
        schedule.default_status is dg.DefaultScheduleStatus.STOPPED for schedule in schedules
    )
    assert research_schedules.public_data_research_wap_job.name == "public_data_research_wap_job"


def test_dbt_models_carry_expected_evidence() -> None:
    models = Path(__file__).resolve().parents[1] / "workflows" / "research"

    places_sql = (models / "places/models/places.sql").read_text(encoding="utf-8")
    assert "upper(trim(name)) as place_name" in places_sql
    assert "inner join geo g" in places_sql
    assert "{{ source('public_raw', 'places_registry') }}" in places_sql
    assert "{{ source('public_raw', 'places_geo') }}" in places_sql

    staging_sql = (models / "indicators/models/stg_observations.sql").read_text(encoding="utf-8")
    assert "(temp_c - 32.0) * 5.0 / 9.0" in staging_sql
    assert "case when unit_f" in staging_sql

    monthly_sql = (models / "indicators/models/monthly_indicators.sql").read_text(encoding="utf-8")
    assert "avg(temp_c)" in monthly_sql
    assert "sum(precip_mm)" in monthly_sql
    assert "obs_month" in monthly_sql

    rollup_sql = (models / "indicators/models/annual_rollup.sql").read_text(encoding="utf-8")
    assert "precip_delta" in rollup_sql
    assert "d.region = p.region" in rollup_sql
    assert "year(r.census_year)" in rollup_sql
    assert "{{ ref('monthly_indicators') }}" not in rollup_sql  # bounded self-joins only


# ---------------------------------------------------------------------------
# Archive reader behavior
# ---------------------------------------------------------------------------


def test_archive_reader_scopes_to_requested_month(data_dir: Path) -> None:
    may = read_month_archive("2026-05", data_dir / "weather")
    assert set(may["station_id"]) == {"P1", "P2", "P3", "P4"}
    assert (may["observed_at"].dt.strftime("%Y-%m") == "2026-05").all()
    with pytest.raises(FileNotFoundError, match="2026-08"):
        read_month_archive("2026-08", data_dir / "weather")


def test_zip_members_read_back_as_csv_frames(data_dir: Path) -> None:
    with zipfile.ZipFile(data_dir / "weather" / f"weather-{DRIFT_MONTH}.zip") as archive:
        member = sorted(archive.namelist())[0]
        frame = pd.read_csv(io.BytesIO(archive.read(member)))
    assert list(frame.columns) == [
        "station_id",
        "observed_at",
        "temp_c",
        "precip_mm",
        "unit_f",
        "pressure_hpa",
    ]
