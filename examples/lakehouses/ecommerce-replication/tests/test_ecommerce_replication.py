"""Fast deterministic contract tests for the e-commerce replication example."""

from __future__ import annotations

import json
from pathlib import Path

import dagster as dg
import pandas as pd
import pytest
from phlo_sling import get_sling_assets

from scripts.generate_fixtures import build_update_set, generate, write_failure_fixtures
from workflows.domains.customers.checks import (
    assert_watermark_never_regresses,
    current_customers,
    validate_customers,
)
from workflows.domains.orders.checks import (
    assert_order_line_integrity,
    assert_payment_reconciliation,
    assert_watermark_advances,
)
from workflows.schedules import commerce as commerce_schedules
from workflows.sources.commerce_postgres import streams


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    data = tmp_path_factory.mktemp("fixtures") / "data"
    counts = generate(data, "test")
    assert counts["orders"] == 14 * 8
    build_update_set(data, "test")
    write_failure_fixtures(data)
    return data


def _read(data_dir: Path, stage: str, name: str) -> pd.DataFrame:
    return pd.read_csv(data_dir / stage / f"{name}.csv")


def test_fixtures_are_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate(first, "test")
    generate(second, "test")
    for table in ["customers", "products", "orders", "order_lines", "payments"]:
        assert (first / "base" / f"{table}.csv").read_bytes() == (
            second / "base" / f"{table}.csv"
        ).read_bytes()


def test_update_set_only_contains_watermark_newer_rows(data_dir: Path) -> None:
    base_max = {
        name: _read(data_dir, "base", name).updated_at.max()
        for name in ["customers", "orders", "payments"]
    }
    checked = 0
    for name in ["customers", "orders", "payments"]:
        delta = data_dir / "update" / f"{name}.csv"
        if not delta.exists():  # e.g. payment corrections never fire at test scale
            continue
        updates = _read(data_dir, "update", name)
        assert not updates.empty
        assert updates.updated_at.min() > base_max[name]
        checked += 1
    assert checked >= 1


def test_customer_snapshot_checks_pass_and_fail(data_dir: Path) -> None:
    customers = _read(data_dir, "base", "customers")
    current = validate_customers(customers)
    assert len(current) == len(customers)
    # A second snapshot run appends changed customers; current state stays 1:1.
    evolved = pd.concat([customers, _read(data_dir, "update", "customers")], ignore_index=True)
    assert len(current_customers(evolved)) == len(customers)
    stale = pd.DataFrame(json.loads((data_dir / "failures" / "stale_customer.json").read_text()))
    previous = validate_customers(customers).updated_at.max()
    with pytest.raises(ValueError, match="watermark regression"):
        assert_watermark_never_regresses(previous, stale)
    assert validate_customers(customers).updated_at.max() == previous


def test_order_line_and_payment_checks_pass_on_base_state(data_dir: Path) -> None:
    orders = _read(data_dir, "base", "orders")
    lines = _read(data_dir, "base", "order_lines")
    payments = _read(data_dir, "base", "payments")
    assert_order_line_integrity(orders, lines)
    assert_payment_reconciliation(orders, payments)


def test_labeled_failure_cases_break_the_invariants_they_name(data_dir: Path) -> None:
    orders = _read(data_dir, "base", "orders")
    payments = _read(data_dir, "base", "payments")

    orphan_row = json.loads((data_dir / "failures" / "orphan_order_line.json").read_text())
    orphan = pd.DataFrame(orphan_row)
    with pytest.raises(ValueError, match="unknown orders"):
        assert_order_line_integrity(orders, orphan)

    over = pd.DataFrame(json.loads((data_dir / "failures" / "over_payment.json").read_text()))
    with pytest.raises(ValueError, match="exceed order totals"):
        assert_payment_reconciliation(orders, pd.concat([payments, over], ignore_index=True))


def test_watermark_check_blocks_stale_incremental_batch(data_dir: Path) -> None:
    orders = _read(data_dir, "base", "orders")
    watermark = orders.updated_at.max()
    advanced = assert_watermark_advances(watermark, _read(data_dir, "update", "new_orders"))
    assert advanced > watermark
    stale_batch = orders.head(1)
    with pytest.raises(ValueError, match="older than watermark"):
        assert_watermark_advances(watermark, stale_batch)


def test_replication_assets_exercise_all_modes_and_distinct_contracts() -> None:
    assets = {asset.key: asset for asset in get_sling_assets()}
    expected = {
        "sling_commerce_customers",
        "sling_commerce_orders",
        "sling_commerce_order_lines",
        "sling_commerce_payments",
        "sling_commerce_products",
        "sling_commerce_config",
    }
    assert set(assets) == expected
    modes = {key: asset.metadata["mode"] for key, asset in assets.items()}
    assert set(modes.values()) == {"snapshot", "incremental", "full-refresh"}
    assert assets["sling_commerce_order_lines"].metadata["primary_key"] == [
        "order_id",
        "line_id",
    ]
    assert assets["sling_commerce_payments"].run.max_retries == 5
    assert assets["sling_commerce_products"].metadata["group"] == "reference"
    assert assets["sling_commerce_config"].run.freshness_hours == (168, 180)
    assert assets["sling_commerce_customers"].metadata["owner"] == "commerce-crm"


def test_streams_read_source_url_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMERCE_SOURCE_URL", "postgresql://override@elsewhere/db")
    assert streams.source_url() == "postgresql://override@elsewhere/db"
    monkeypatch.delenv("COMMERCE_SOURCE_URL")
    assert streams.source_url().startswith("postgresql://commerce:commerce@localhost:5436")


def test_schedules_have_distinct_cadences_and_default_to_stopped() -> None:
    schedules = (
        commerce_schedules.frequent_incremental_schedule,
        commerce_schedules.nightly_reference_schedule,
        commerce_schedules.weekly_customer_snapshot_schedule,
        commerce_schedules.daily_transform_schedule,
        commerce_schedules.weekly_full_reconciliation_schedule,
    )
    crons = {schedule.cron_schedule for schedule in schedules}
    assert crons == {"*/15 * * * *", "30 2 * * *", "0 3 * * 6", "0 4 * * *", "0 5 * * 1"}
    assert all(
        schedule.default_status is dg.DefaultScheduleStatus.STOPPED for schedule in schedules
    )


def test_dbt_models_carry_the_expected_evidence() -> None:
    models_dir = Path(__file__).resolve().parents[1] / "workflows/transforms/dbt/models"
    facts = (models_dir / "order_lifecycle_facts.sql").read_text(encoding="utf-8")
    assert "raw_order_lines" in facts and "group by order_id" in facts
    assert "row_number() over (partition by order_id order by updated_at desc)" in facts
    dimension = (models_dir / "customer_dimension.sql").read_text(encoding="utf-8")
    assert "partition by customer_id" in dimension
    assert "order by updated_at desc" in dimension
    assert "_phlo_ingested_at" not in dimension
    reconciliation = (models_dir / "payment_reconciliation.sql").read_text(encoding="utf-8")
    assert "reconciliation_status" in reconciliation
