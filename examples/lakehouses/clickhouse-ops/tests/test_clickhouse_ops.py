"""Fast deterministic contract tests for the ClickHouse operational example."""

from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

import dagster as dg
import pandas as pd
import phlo_clickhouse.plugin as ch_plugin
import pytest
import yaml
from phlo_dlt import get_ingestion_assets
from phlo_sling import get_sling_assets

from scripts.generate_fixtures import (
    ALLOWED_STATUS_CODES,
    DAY,
    OPERATING_HOURS,
    TENANTS,
    TIER1_TENANTS,
    generate,
)
from workflows.access_logs.ingest import read_access_logs
from workflows.accounts import streams
from workflows.platform_events.ingest import (
    read_platform_events,
    with_occurred_hour,
)
from workflows.quality.validators import (
    assert_hourly_matches_daily,
    assert_latency_within_bounds,
    assert_status_codes_known,
    assert_tier1_tenant_freshness,
    hourly_p95,
    latest_versions,
)
from workflows.schedules import ops as ops_schedules
from workflows.schemas.contracts import AccessLogSchema, PlatformEventSchema


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    data = tmp_path_factory.mktemp("fixtures") / "generated-data"
    generate(data)
    return data


def _read_ndjson_gz(path: Path) -> pd.DataFrame:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return pd.DataFrame(rows)


def _tree_hash(root: Path) -> list[tuple[str, bytes]]:
    return sorted(
        (str(path.relative_to(root)), path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    )


def test_fixtures_are_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate(first)
    generate(second)
    assert _tree_hash(first) == _tree_hash(second)


def test_platform_events_pass_contract_and_keep_replays(data_dir: Path) -> None:
    raw = read_platform_events(data_dir / "platform_events")
    validated = PlatformEventSchema.validate(with_occurred_hour(raw))
    # 48 distinct events (4 hours x 3 tenants x 4 slots) + 12 verbatim replays.
    assert len(raw) == 60
    assert raw["event_id"].nunique() == 48
    replayed = raw[raw.duplicated(subset="event_id", keep=False)]
    assert len(replayed) > 0
    # Replays are verbatim: identical payload values per duplicated id.
    grouped = replayed.groupby("event_id")[["tenant_id", "occurred_at", "latency_ms"]].nunique()
    assert (grouped == 1).all().all()
    assert_latency_within_bounds(validated)
    assert_tier1_tenant_freshness(validated)


def test_access_logs_pass_contract_with_exact_p95_rank(data_dir: Path) -> None:
    logs = read_access_logs(data_dir / "access_logs")
    validated = AccessLogSchema.validate(logs)
    assert len(validated) == 21 * len(OPERATING_HOURS)
    assert set(validated["status_code"]).issubset(set(ALLOWED_STATUS_CODES))
    assert set(validated["tenant_id"]) >= set(TIER1_TENANTS)
    hour_floor = pd.to_datetime(validated["occurred_at"]).dt.floor("h")
    for _, hour_frame in validated.groupby(hour_floor):
        # Distinct durations per hour make the p95 unambiguous.
        assert hour_frame["duration_ms"].nunique() == len(hour_frame)
        rank_position = 0.95 * (len(hour_frame) - 1)
        assert rank_position == int(rank_position)


def test_labeled_failure_breaks_only_latency_bounds(data_dir: Path) -> None:
    invalid = _read_ndjson_gz(
        data_dir / "failures" / "platform_events_latency_out_of_bounds.ndjson.gz"
    )
    with pytest.raises(ValueError, match="latency_ms"):
        assert_latency_within_bounds(invalid)
    # The same row is otherwise contract-clean: only the bound is broken.
    with pytest.raises(Exception, match="latency_ms"):
        PlatformEventSchema.validate(with_occurred_hour(invalid))


def test_labeled_failure_breaks_only_status_catalog(data_dir: Path) -> None:
    invalid = _read_ndjson_gz(data_dir / "failures" / "access_logs_status_code_unknown.ndjson.gz")
    with pytest.raises(ValueError, match="status_code"):
        assert_status_codes_known(invalid)
    with pytest.raises(Exception, match="status_code"):
        AccessLogSchema.validate(invalid)


def test_labeled_failure_breaks_only_tier1_freshness(data_dir: Path) -> None:
    gap = _read_ndjson_gz(data_dir / "failures" / "platform_events_tier1_gap.ndjson.gz")
    with pytest.raises(ValueError, match="tier-1 tenant freshness gap"):
        assert_tier1_tenant_freshness(gap)
    # Tier-2 traffic alone does not trip the other event validators.
    assert_latency_within_bounds(gap)


def test_labeled_failure_breaks_only_count_reconciliation(data_dir: Path) -> None:
    hourly, honest_daily = _fixture_aggregates(data_dir)
    assert_hourly_matches_daily(hourly, honest_daily)

    shortfall_path = data_dir / "failures" / "reconciliation_shortfall.csv"
    with shortfall_path.open(encoding="utf-8") as handle:
        tampered = pd.DataFrame(csv.DictReader(handle))
    for metric in ("event_count", "request_count", "error_count"):
        tampered[metric] = tampered[metric].astype(int)
    with pytest.raises(ValueError, match="count reconciliation mismatch"):
        assert_hourly_matches_daily(hourly, tampered)


def _fixture_aggregates(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pandas mirror of the dbt marts: dedup, hourly aggregates, daily totals."""
    events = latest_versions(
        read_platform_events(data_dir / "platform_events"),
        key="event_id",
        order_by=["occurred_at"],
    )
    logs = latest_versions(
        read_access_logs(data_dir / "access_logs"),
        key="request_id",
        order_by=["occurred_at"],
    )
    logs["event_hour"] = pd.to_datetime(logs["occurred_at"]).dt.floor("h")

    def error_count(series: pd.Series) -> int:
        return int((series >= 500).sum())

    hourly = logs.groupby(["event_hour", "tenant_id"], as_index=False).agg(
        request_count=("request_id", "count"),
        error_count=("status_code", error_count),
    )
    daily = (
        events.groupby("tenant_id", as_index=False).size().rename(columns={"size": "event_count"})
    )
    log_daily = logs.groupby("tenant_id", as_index=False).agg(
        request_count=("request_id", "count"),
        error_count=("status_code", error_count),
    )
    honest_daily = log_daily.merge(daily, on="tenant_id", how="outer").fillna(0)
    honest_daily.insert(0, "usage_date", DAY)
    honest_daily[["event_count", "request_count", "error_count"]] = honest_daily[
        ["event_count", "request_count", "error_count"]
    ].astype(int)

    # The validator reconciles every metric it is given; give it the full
    # hourly grain including platform-event counts, mirroring the marts.
    event_hourly = events.copy()
    event_hourly["event_hour"] = pd.to_datetime(event_hourly["occurred_at"]).dt.floor("h")
    event_counts = event_hourly.groupby(["event_hour", "tenant_id"], as_index=False).agg(
        event_count=("event_id", "count")
    )
    hourly = hourly.merge(event_counts, on=["event_hour", "tenant_id"], how="outer").fillna(0)
    hourly[["request_count", "error_count", "event_count"]] = hourly[
        ["request_count", "error_count", "event_count"]
    ].astype(int)
    return hourly, honest_daily


def test_reconciliation_holds_exactly_on_fixture_arithmetic(data_dir: Path) -> None:
    hourly, daily = _fixture_aggregates(data_dir)
    assert_hourly_matches_daily(hourly, daily)
    per_tenant = daily.set_index("tenant_id")
    # Every tenant sees 16 events and 28 requests; errors are fixed by the
    # status formula (10 across the day).
    assert (per_tenant["event_count"] == 16).all()
    assert (per_tenant["request_count"] == 28).all()
    assert int(per_tenant["error_count"].sum()) == 10
    assert sorted(per_tenant.index) == sorted(t[0] for t in TENANTS)


def test_p95_quantile_is_exact_and_stable_under_replay(data_dir: Path) -> None:
    logs = read_access_logs(data_dir / "access_logs")
    deduped_once = latest_versions(logs, key="request_id", order_by=["occurred_at"])
    hours = pd.to_datetime(deduped_once["occurred_at"]).dt.floor("h")
    p95_first = {
        str(hour): hourly_p95(frame["duration_ms"]) for hour, frame in deduped_once.groupby(hours)
    }
    # Exact expected values from the fixture arithmetic: with 21 distinct
    # durations the p95 is the second-largest sample of each hour.
    expected = {}
    for hour_index in range(len(OPERATING_HOURS)):
        durations = sorted(40 + (((hour_index * 97 + r * 61) % 1200) * 5) for r in range(21))
        expected[str(pd.Timestamp(f"{DAY}T{hour_index:02d}:00:00"))] = durations[19]
    assert p95_first == expected

    # Replay every delivery twice; read-time dedup keeps p95 identical.
    doubled = pd.concat([deduped_once, deduped_once], ignore_index=True)
    deduped_twice = latest_versions(doubled, key="request_id", order_by=["occurred_at"])
    hours_twice = pd.to_datetime(deduped_twice["occurred_at"]).dt.floor("h")
    p95_second = {
        str(hour): hourly_p95(frame["duration_ms"])
        for hour, frame in deduped_twice.groupby(hours_twice)
    }
    assert p95_second == p95_first
    assert len(deduped_twice) == len(deduped_once)


def test_event_marts_are_replay_idempotent(data_dir: Path) -> None:
    raw = read_platform_events(data_dir / "platform_events")
    once = latest_versions(raw, key="event_id", order_by=["occurred_at"])
    doubled = pd.concat([raw, raw], ignore_index=True)
    twice = latest_versions(doubled, key="event_id", order_by=["occurred_at"])
    assert len(once) == 48
    assert len(twice) == len(once)
    assert (
        twice.sort_values("event_id")["latency_ms"].tolist()
        == once.sort_values("event_id")["latency_ms"].tolist()
    )


def test_ingestion_assets_carry_differentiated_contracts() -> None:
    assets = {asset.key: asset for asset in get_ingestion_assets()}
    assert set(assets) == {"dlt_platform_events", "dlt_access_logs"}
    events = assets["dlt_platform_events"]
    logs = assets["dlt_access_logs"]
    assert events.metadata["write_mode"] == "append"
    assert events.metadata["primary_key"] == ["event_id"]
    assert events.metadata["owner"] == "platform-observability"
    assert logs.metadata["primary_key"] == ["request_id"]
    assert events.run.freshness_hours != logs.run.freshness_hours
    assert all(asset.checks[0].blocking for asset in assets.values())


def test_accounts_stream_targets_clickhouse_via_explicit_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sling_assets = {asset.key: asset for asset in get_sling_assets()}
    assert "sling_chmeta_tenants" in sling_assets

    monkeypatch.delenv("CHMETA_SOURCE_URL", raising=False)
    assert streams.source_url().startswith("postgresql://chmeta:chmeta@localhost:10832/chmeta")

    monkeypatch.setenv("CHMETA_SOURCE_URL", "postgresql://override@elsewhere/chmeta")
    assert streams.source_url() == "postgresql://override@elsewhere/chmeta"

    monkeypatch.setenv("CHMETA_TARGET_CONN", "PHLO_CLICKHOUSE_CONN")
    assert streams.clickhouse_target() == "PHLO_CLICKHOUSE_CONN"


def test_capability_routing_names_clickhouse_for_all_three_roles() -> None:
    provider = ch_plugin.ClickHouseResourceProvider()
    stores = [spec.name for spec in provider.get_table_stores()]
    engines = [spec.name for spec in provider.get_query_engines()]
    targets = [spec.name for spec in provider.get_publish_targets()]
    assert stores == engines == targets == ["clickhouse"]
    store_support = provider.get_table_stores()[0].support
    assert store_support.supports_snapshots is False
    engine_support = provider.get_query_engines()[0].support
    assert engine_support.supports_snapshots is False
    assert engine_support.supports_time_travel is False


def test_project_routes_clickhouse_and_disables_wap() -> None:
    config = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "phlo.yaml").read_text(encoding="utf-8")
    )
    defaults = config["capabilities"]["defaults"]
    assert defaults["table_store"] == "clickhouse"
    assert defaults["query_engine"] == "clickhouse"
    assert defaults["publish_target"] == "clickhouse"
    assert config["wap"]["enabled"] is False


def test_schedules_cover_micro_batch_refresh_metadata_and_stop() -> None:
    registered = (
        ops_schedules.micro_batch_schedule,
        ops_schedules.hourly_mart_refresh_schedule,
        ops_schedules.nightly_metadata_schedule,
    )
    assert {schedule.cron_schedule for schedule in registered} == {
        "*/15 * * * *",
        "10 * * * *",
        "30 2 * * *",
    }
    assert len({schedule.job.name for schedule in registered}) == len(registered)
    assert all(
        schedule.default_status is dg.DefaultScheduleStatus.STOPPED for schedule in registered
    )


def test_dbt_models_carry_dedup_append_replacing_evidence() -> None:
    models_dir = Path(__file__).resolve().parents[1] / "workflows/operational_marts/dbt/models"

    event_dedup = (models_dir / "stg_platform_events_dedup.sql").read_text(encoding="utf-8")
    assert "partition by e.event_id" in event_dedup
    assert "order by e.occurred_at desc" in event_dedup
    log_dedup = (models_dir / "stg_access_logs_dedup.sql").read_text(encoding="utf-8")
    assert "row_number() over (" in log_dedup and "version_rank = 1" in log_dedup

    error_rate = (models_dir / "error_rate_hourly.sql").read_text(encoding="utf-8")
    assert "incremental_strategy='append'" in error_rate
    assert "countIf(status_code >= 500)" in error_rate
    throughput = (models_dir / "throughput_hourly.sql").read_text(encoding="utf-8")
    assert "incremental_strategy='append'" in throughput
    latency = (models_dir / "latency_p95_hourly.sql").read_text(encoding="utf-8")
    assert "quantileExact(0.95)(duration_ms)" in latency

    usage = (models_dir / "tenant_usage_daily.sql").read_text(encoding="utf-8")
    assert "ReplacingMergeTree()" in usage
    assert "order_by='(usage_date, tenant_id)'" in usage

    sources = (models_dir / "sources.yml").read_text(encoding="utf-8")
    assert "phlo_asset_key: dlt_platform_events" in sources
    assert "phlo_asset_key: dlt_access_logs" in sources
    assert "phlo_asset_key: sling_chmeta_tenants" in sources

    profile = (models_dir.parent / "profiles/profiles.yml").read_text(encoding="utf-8")
    parsed = yaml.safe_load(profile)
    output = parsed["clickhouse_ops"]["outputs"]["dev"]
    assert output["type"] == "clickhouse"
    assert output["schema"] == "marts"
    project = yaml.safe_load((models_dir.parent / "dbt_project.yml").read_text(encoding="utf-8"))
    assert project["profile"] == "clickhouse_ops"
    phlo_env = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "phlo.yaml").read_text(encoding="utf-8")
    )["env"]
    assert phlo_env["DBT_QUERY_ENGINE_TYPE"] == "clickhouse"
