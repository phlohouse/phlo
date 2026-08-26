"""Fast deterministic contract tests for the logistics control tower example."""

from __future__ import annotations

import json
from pathlib import Path

import dagster as dg
import pandas as pd
import pytest
from phlo_dlt import get_ingestion_assets
from phlo_sling import get_sling_assets

from scripts.carrier_api import serve as serve_replay
from scripts.generate_fixtures import (
    CONTRADICTION_EXCEPTION_AT,
    WATERMARK,
    build_update_set,
    generate,
    write_failure_fixtures,
)
from workflows.carriers.ingestion import assert_known_carrier_reference
from workflows.carriers.transforms import carrier_metrics
from workflows.control_tower.transforms import shipment_grid, state_logic
from workflows.orders.replication import DEFAULT_SOURCE_URL, source_url
from workflows.orders.transforms import order_state
from workflows.schedules import logistics as logistics_schedules
from workflows.warehouses.ingestion import read_warehouse_scans
from workflows.warehouses.transforms import scan_metrics


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    data = tmp_path_factory.mktemp("fixtures") / "generated-data"
    counts = generate(data)
    assert counts == {
        "order_versions": 27,
        "distinct_orders": 24,
        "shipments": 18,
        "carrier_events": 54,
        "warehouse_scans": 36,
        "warehouses": 3,
        "carriers": 2,
    }
    build_update_set(data)
    write_failure_fixtures(data)
    return data


def _read_csv(data_dir: Path, *parts: str) -> pd.DataFrame:
    return pd.read_csv(data_dir.joinpath(*parts), dtype=str)


def _load_events(data_dir: Path, *parts: str) -> pd.DataFrame:
    payload = json.loads(data_dir.joinpath(*parts).read_text())
    return pd.DataFrame(payload["events"])


def _all_events(data_dir: Path) -> pd.DataFrame:
    frames = [
        _load_events(data_dir, "carriers", carrier, f"{day}.json")
        for carrier in ("ATLAS", "CORSAIR")
        for day in ("2026-08-10", "2026-08-11", "2026-08-12")
    ]
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# Fixture determinism and update-set semantics
# ---------------------------------------------------------------------------


def _tree(root: Path) -> list[tuple[str, bytes]]:
    return sorted(
        (str(path.relative_to(root)), path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    )


def test_fixtures_are_byte_stable(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    for target in (first, second):
        generate(target)
        build_update_set(target)
        write_failure_fixtures(target)
    assert _tree(first) == _tree(second)


def test_update_set_only_contains_watermark_newer_rows(data_dir: Path) -> None:
    base = _read_csv(data_dir, "base", "orders.csv")
    latest = base.sort_values("updated_at", kind="stable").drop_duplicates("order_id", keep="last")
    watermark_by_order = dict(zip(latest["order_id"], latest["updated_at"]))

    updates = _read_csv(data_dir, "update", "orders.csv")
    checked_existing = 0
    for row in updates.itertuples(index=False):
        previous = watermark_by_order.get(row.order_id)
        if previous is not None:
            assert row.updated_at > previous, row
            checked_existing += 1
        else:
            assert row.updated_at > WATERMARK.isoformat(), row
    assert checked_existing >= 4


# ---------------------------------------------------------------------------
# Orders domain: version collapse and status regression gate
# ---------------------------------------------------------------------------


def test_orders_status_gate_passes_on_base_and_fails_on_labeled_regression(
    data_dir: Path,
) -> None:
    base = _read_csv(data_dir, "base", "orders.csv")
    assert order_state.assert_status_never_regresses(base) is None

    regression = _read_csv(data_dir, "failures", "orders_status_regression.csv")
    violation = order_state.assert_status_never_regresses(regression)
    assert violation is not None
    assert "ORD-9001" in violation
    assert "shipped" in violation


def test_latest_order_versions_collapses_to_one_row_per_order(data_dir: Path) -> None:
    base = _read_csv(data_dir, "base", "orders.csv")
    current = order_state.latest_order_versions(base)
    assert len(current) == 24
    ord_1002 = current[current["order_id"] == "ORD-1002"].iloc[0]
    assert ord_1002["current_status"] == "delivered"


def test_source_url_defaults_to_compose_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOGISTICS_SOURCE_URL", "postgresql://override@elsewhere/db")
    assert source_url() == "postgresql://override@elsewhere/db"
    monkeypatch.delenv("LOGISTICS_SOURCE_URL")
    assert source_url() == DEFAULT_SOURCE_URL
    assert ":10332/" in source_url()


# ---------------------------------------------------------------------------
# Carriers domain: ingestion gate and coverage
# ---------------------------------------------------------------------------


def test_carrier_gate_passes_on_fixtures_and_fails_on_unknown_carrier(data_dir: Path) -> None:
    events = _all_events(data_dir)
    assert assert_known_carrier_reference(events) is None

    unknown = _load_events(data_dir, "failures", "events_unknown_carrier.json")
    violation = assert_known_carrier_reference(unknown)
    assert violation is not None
    assert "ZEPHYR" in violation


def test_carrier_coverage_splits_volume_across_both_feeds(data_dir: Path) -> None:
    events = _all_events(data_dir)
    events["event_time"] = pd.to_datetime(events["event_time"], utc=True)
    coverage = carrier_metrics.compute_carrier_coverage(events)
    by_carrier = coverage.set_index("carrier")["event_count"].to_dict()
    assert by_carrier == {"ATLAS": 27, "CORSAIR": 27}
    assert set(coverage["distinct_shipments"]) == {9}


# ---------------------------------------------------------------------------
# Control tower: canonical state ordering and SLA arithmetic
# ---------------------------------------------------------------------------


def test_canonical_state_resolves_contradiction_by_event_time(data_dir: Path) -> None:
    events = _all_events(data_dir)
    events["event_time"] = pd.to_datetime(events["event_time"], utc=True)

    resolved = state_logic.resolve_canonical_states(events).set_index("shipment_id")

    # Contradiction fixture: delivered first, exception later; later wins and
    # the contradiction stays visible.
    contradiction = resolved.loc["SHP-2018"]
    assert contradiction["canonical_state"] == "exception"
    assert contradiction["state_as_of"] == pd.Timestamp(CONTRADICTION_EXCEPTION_AT)
    assert contradiction["contradiction_count"] == 1

    # Recovery fixture: exception cleared by a later delivered event.
    recovered = resolved.loc["SHP-2017"]
    assert recovered["canonical_state"] == "delivered"
    assert recovered["contradiction_count"] == 1

    # Clean shipments never carry a contradiction.
    clean = resolved.drop(index=["SHP-2017", "SHP-2018"])
    assert (clean["contradiction_count"] == 0).all()
    assert (clean.loc[clean["canonical_state"] == "exception"]).empty


def test_ambiguous_state_fixture_breaks_the_ordering_invariant(data_dir: Path) -> None:
    ambiguous = _load_events(data_dir, "failures", "events_ambiguous_state.json")
    ambiguous["event_time"] = pd.to_datetime(ambiguous["event_time"], utc=True)
    with pytest.raises(ValueError, match="ambiguous canonical state"):
        state_logic.assert_unambiguous_event_order(ambiguous)


def test_transit_hours_and_sla_match_fixture_numbers_exactly(data_dir: Path) -> None:
    events = _all_events(data_dir)
    events["event_time"] = pd.to_datetime(events["event_time"], utc=True)

    transit = state_logic.compute_transit_hours(events).set_index("shipment_id")
    # Delivered fixtures only: 16 normal shipments plus the recovery shipment.
    assert len(transit) == 17
    assert "SHP-2018" not in transit.index  # contradiction keeps it out of SLA marts

    # Pinned arithmetic: pickup + (20 + (index % 5) * 3) hours.
    assert float(transit.loc["SHP-2002", "transit_hours"]) == 26.0
    assert float(transit.loc["SHP-2014", "transit_hours"]) == 32.0

    terms = {"ATLAS": 26.0, "CORSAIR": 30.0}  # standard service levels
    for shipment_id, row in transit.iterrows():
        verdict = state_logic.evaluate_sla(float(row["transit_hours"]), terms[row["carrier"]])
        expected_breach = float(row["transit_hours"]) > terms[row["carrier"]]
        assert verdict["sla_breached"] is expected_breach
        if shipment_id == "SHP-2014":
            assert verdict == {"sla_breached": True, "breach_hours": 6.0}

    breached = {
        shipment_id
        for shipment_id, row in transit.iterrows()
        if float(row["transit_hours"]) > terms[row["carrier"]]
    }
    assert len(breached) == 4


def test_negative_sla_clock_fails_the_validator(data_dir: Path) -> None:
    negative = _read_csv(data_dir, "failures", "sla_terms_negative.csv")
    violation = state_logic.assert_sla_clock_positive(negative)
    assert violation is not None
    assert "-6" in violation

    reference = _read_csv(data_dir, "reference", "sla_terms.csv")
    assert state_logic.assert_sla_clock_positive(reference) is None


# ---------------------------------------------------------------------------
# Warehouses domain: dwell computation
# ---------------------------------------------------------------------------


def test_warehouse_dwell_pairs_scans_and_flags_open_shipments(data_dir: Path) -> None:
    scans = read_warehouse_scans(data_dir / "warehouses")
    assert len(scans) == 36

    dwell = scan_metrics.compute_dwell(scans).set_index("shipment_id")
    assert len(dwell) == 18
    # Dwell hours follow the generator lane pattern 4/6/8/10.
    assert sorted({float(hours) for hours in dwell["dwell_hours"]}) == [4.0, 6.0, 8.0, 10.0]

    anomalies = scan_metrics.build_scan_anomalies(scans)
    assert anomalies.empty  # every fixture shipment closes its outbound scan


def test_shipment_grid_converges_all_three_domains(data_dir: Path) -> None:
    events = _all_events(data_dir)
    events["event_time"] = pd.to_datetime(events["event_time"], utc=True)
    exceptions = carrier_metrics.build_shipment_exceptions(events)
    dwell = scan_metrics.compute_dwell(read_warehouse_scans(data_dir / "warehouses"))
    orders = order_state.latest_order_versions(_read_csv(data_dir, "base", "orders.csv"))

    grid = shipment_grid.build_shipment_grid(orders, events, exceptions, dwell)
    assert len(grid) == 18  # every shipment appears exactly once
    assert not grid["customer_ref"].isna().any()  # order join is complete
    assert set(grid["carriers_seen"]) == {1}  # one carrier per fixture lane
    assert int(grid["has_exception"].sum()) == 1  # only SHP-2018 stays open
    assert grid.loc[grid["shipment_id"] == "SHP-2018", "has_exception"].all()
    assert not grid["dwell_hours"].isna().any()  # every shipment closed its scans


# ---------------------------------------------------------------------------
# Asset registration across repeated transform folders
# ---------------------------------------------------------------------------

TRANSFORM_MODULES = {
    "orders": order_state,
    "carriers": carrier_metrics,
    "warehouses": scan_metrics,
    "control_tower": shipment_grid,
}


def _module_assets(module) -> dict[str, dg.AssetsDefinition]:
    return {
        name: value
        for name, value in vars(module).items()
        if isinstance(value, dg.AssetsDefinition)
    }


def test_all_four_transform_folders_register_real_assets() -> None:
    contributed = 0
    for domain, module in TRANSFORM_MODULES.items():
        assets = _module_assets(module)
        assert assets, f"{domain} transform folder registers no assets"
        contributed += 1
        for asset in assets.values():
            keys = asset.keys
            assert keys, asset
    assert contributed >= 4


def test_expected_python_transform_asset_names() -> None:
    names = set()
    for module in TRANSFORM_MODULES.values():
        for asset in _module_assets(module).values():
            names |= {key.to_user_string() for key in asset.keys}
    assert names == {
        "order_current_state",
        "carrier_events_unified",
        "shipment_exceptions",
        "carrier_coverage",
        "warehouse_dwell",
        "warehouse_scan_exceptions",
        "control_tower_shipment_grid",
    }


def test_name_collision_is_deliberately_resolved() -> None:
    """Both exception views existed under one name once; exactly one survives."""
    key_counts: dict[str, int] = {}
    for module in TRANSFORM_MODULES.values():
        for asset in _module_assets(module).values():
            for key in asset.keys:
                key_counts[key.to_user_string()] = key_counts.get(key.to_user_string(), 0) + 1
    assert key_counts["shipment_exceptions"] == 1
    assert key_counts["warehouse_scan_exceptions"] == 1
    # The resolution is documented in both modules' docstrings.
    assert "collision" in (carrier_metrics.__doc__ or "").lower()
    assert "collision" in (scan_metrics.__doc__ or "").lower()


def test_control_tower_grid_depends_on_all_three_domains() -> None:
    assets = _module_assets(shipment_grid)
    grid = next(iter(assets.values()))
    dep_keys = {key.to_user_string() for key in grid.dependency_keys}
    assert {
        "order_current_state",
        "carrier_events_unified",
        "shipment_exceptions",
        "warehouse_dwell",
    } <= dep_keys


def test_unified_carrier_feed_depends_on_both_ingestion_assets() -> None:
    unified = _module_assets(carrier_metrics)["carrier_events_unified"]
    dep_keys = {key.to_user_string() for key in unified.dependency_keys}
    assert {"dlt_carrier_events_atlas", "dlt_carrier_events_corsair"} <= dep_keys


# ---------------------------------------------------------------------------
# Ingestion contracts are differentiated per source behavior
# ---------------------------------------------------------------------------


def test_ingestion_assets_carry_differentiated_contracts() -> None:
    import workflows.carriers.ingestion  # noqa: F401 - registration side effect
    import workflows.warehouses.ingestion  # noqa: F401 - registration side effect

    assets = {asset.key: asset for asset in get_ingestion_assets()}
    assert set(assets) == {
        "dlt_carrier_events_atlas",
        "dlt_carrier_events_corsair",
        "dlt_carrier_directory",
        "dlt_sla_terms",
        "dlt_warehouse_scans",
    }

    atlas = assets["dlt_carrier_events_atlas"]
    corsair = assets["dlt_carrier_events_corsair"]
    for feed in (atlas, corsair):
        assert feed.metadata["write_mode"] == "merge"
        assert feed.metadata["primary_key"] == ["event_id"]
        assert feed.metadata["owner"] == "logistics-carrier-ops"
        assert feed.run.freshness_hours == (3, 6)
        assert any(check.blocking for check in feed.checks)

    directory = assets["dlt_carrier_directory"]
    sla_terms = assets["dlt_sla_terms"]
    for reference in (directory, sla_terms):
        assert reference.metadata["write_mode"] == "merge"
        assert reference.run.freshness_hours == (168, 192)
        assert reference.partitions is None  # partitioned=False reference merges
    assert directory.metadata["group"] == "carriers_reference"

    scans = assets["dlt_warehouse_scans"]
    assert scans.metadata["write_mode"] == "merge"
    assert scans.metadata["primary_key"] == ["scan_id"]
    assert scans.run.freshness_hours == (8, 12)


def test_sling_orders_stream_is_incremental_on_updated_at() -> None:
    import workflows.orders.replication  # noqa: F401 - registration side effect

    assets = {asset.key: asset for asset in get_sling_assets()}
    assert set(assets) == {"sling_shipments_orders"}
    orders = assets["sling_shipments_orders"]
    assert orders.metadata["mode"] == "incremental"
    assert orders.metadata["primary_key"] == ["order_id"]
    assert orders.metadata["owner"] == "logistics-fulfillment"
    assert orders.metadata["table_name"] == "shipments_orders"


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------


def test_schedules_have_distinct_cadences_and_default_to_stopped() -> None:
    schedules = (
        logistics_schedules.orders_incremental_schedule,
        logistics_schedules.atlas_polling_schedule,
        logistics_schedules.corsair_polling_schedule,
        logistics_schedules.reference_refresh_schedule,
        logistics_schedules.daily_marts_schedule,
        logistics_schedules.weekly_reconciliation_schedule,
    )
    assert len(schedules) == len({schedule.name for schedule in schedules})
    assert len({schedule.cron_schedule for schedule in schedules}) >= 5
    assert all(
        schedule.default_status == dg.DefaultScheduleStatus.STOPPED for schedule in schedules
    )
    # Carrier feeds poll on different cadences: hourly vs every four hours.
    assert logistics_schedules.atlas_polling_schedule.cron_schedule == "10 * * * *"
    assert logistics_schedules.corsair_polling_schedule.cron_schedule == "35 */4 * * *"


# ---------------------------------------------------------------------------
# dbt models carry the ordering and SLA evidence
# ---------------------------------------------------------------------------


def test_dbt_models_carry_the_expected_evidence() -> None:
    models_dir = (
        Path(__file__).resolve().parents[1] / "workflows/control_tower/transforms/dbt/models"
    )
    canonical = (models_dir / "canonical_shipment_state.sql").read_text()
    assert "source('logistics_raw', 'carrier_events')" in canonical
    assert "max(event_time)" in canonical
    assert "severity_rank" in canonical
    assert "contradiction_count" in canonical

    transit = (models_dir / "transit_duration.sql").read_text()
    assert "date_diff('minute'" in transit
    assert "ref('canonical_shipment_state')" in transit

    sla_mart = (models_dir / "sla_mart.sql").read_text()
    assert "sla_breached" in sla_mart
    assert "breach_hours" in sla_mart
    assert "service_level = 'standard'" in sla_mart

    sources = (models_dir / "sources.yml").read_text()
    assert "phlo_asset_key: sling_shipments_orders" in sources
    assert "phlo_asset_key: carrier_events_unified" in sources


# ---------------------------------------------------------------------------
# Replay API determinism
# ---------------------------------------------------------------------------


def test_replay_api_serves_fixture_bytes(data_dir: Path) -> None:
    server = serve_replay(data_dir=data_dir, port=0)
    try:
        port = server.server_address[1]
        import urllib.request

        with urllib.request.urlopen(  # noqa: S310 - local replay endpoint
            f"http://127.0.0.1:{port}/v1/events?carrier=ATLAS&date=2026-08-11", timeout=5
        ) as response:
            served = json.load(response)
        fixture = json.loads((data_dir / "carriers" / "ATLAS" / "2026-08-11.json").read_text())
        assert served == fixture
        assert all(event["carrier"] == "ATLAS" for event in served["events"])
    finally:
        server.shutdown()
