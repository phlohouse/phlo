from pathlib import Path

import dagster as dg
import pandas as pd
import pandera.pandas as pa
import pytest
from phlo_dlt import get_ingestion_assets

from scripts.generate_fixtures import generate
from workflows.ingestion.retail import files as retail_files
from workflows.ingestion.retail.files import read_inventory, read_reference, read_sales
from workflows.quality.retail import validate_retail
from workflows.schedules import retail as retail_schedules


def fixture(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    generate(data, "test")
    return data


def test_test_scale_and_completeness(tmp_path: Path) -> None:
    data = fixture(tmp_path)
    sales = read_sales(data)
    assert len(sales) == 160 and len(list((data / "sales").rglob("*.csv"))) == 4
    (data / "sales" / "2025-01-01" / "S001.csv").unlink()
    with pytest.raises(FileNotFoundError, match="store files"):
        read_sales(data)


def test_quality_failure_cases(tmp_path: Path) -> None:
    data = fixture(tmp_path)
    sales = read_sales(data)
    products = read_reference(data, "products")
    stores = read_reference(data, "stores")
    promos = read_reference(data, "promotions")
    inventory = read_inventory(data)
    validate_retail(sales, products, stores, promos, inventory)
    for name in ["duplicate_line.csv", "unknown_product.csv", "bad_arithmetic.csv"]:
        with pytest.raises((ValueError, pa.errors.SchemaError)):
            validate_retail(
                pd.read_csv(data / "failures" / name), products, stores, promos, inventory
            )


def test_parquet_and_ndjson_are_used(tmp_path: Path) -> None:
    data = fixture(tmp_path)
    assert (data / "historical_sales.parquet").stat().st_size > 0
    assert len(read_inventory(data)) == 40


def test_assets_have_distinct_operating_contracts() -> None:
    assert retail_files.retail_sales_lines.__module__.startswith("workflows.")
    assets = {asset.key: asset for asset in get_ingestion_assets() if asset.group == "retail"}
    assert set(assets) == {
        "dlt_retail_sales_lines",
        "dlt_retail_products",
        "dlt_retail_stores",
        "dlt_retail_promotions",
        "dlt_retail_inventory",
    }
    assert assets["dlt_retail_sales_lines"].metadata["write_mode"] == "merge"
    assert assets["dlt_retail_inventory"].metadata["write_mode"] == "append"
    assert assets["dlt_retail_sales_lines"].run.max_retries == 3
    assert assets["dlt_retail_inventory"].run.max_retries == 5
    assert assets["dlt_retail_stores"].run.freshness_hours == (168, 192)
    assert assets["dlt_retail_promotions"].metadata["owner"] == "retail-marketing"
    assert all(asset.checks[0].blocking for asset in assets.values())


def test_dagster_schedules_have_distinct_cadences() -> None:
    schedules = (
        retail_schedules.daily_sales_schedule,
        retail_schedules.hourly_inventory_schedule,
        retail_schedules.weekly_reference_schedule,
        retail_schedules.daily_transform_schedule,
        retail_schedules.weekly_full_reconciliation_schedule,
    )
    assert {schedule.cron_schedule for schedule in schedules} == {
        "15 2 * * *",
        "0 * * * *",
        "0 3 * * 0",
        "0 4 * * *",
        "0 5 * * 1",
    }
    assert all(
        schedule.default_status is dg.DefaultScheduleStatus.STOPPED for schedule in schedules
    )
    assert retail_schedules.retail_wap_job.tags == {}


def test_inventory_transform_deduplicates_the_append_ledger() -> None:
    model = (
        Path("workflows/transforms/dbt/models/inventory_balances.sql")
        .read_text(encoding="utf-8")
        .lower()
    )
    assert "partition by inventory_snapshot_id" in model
    assert "order by _phlo_ingested_at desc" in model
    assert 'var("partition_date_str")' in model
