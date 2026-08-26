"""Schedules that reflect the arrival cadence of each retail source."""

import dagster as dg

retail_wap_job = dg.define_asset_job(
    name="retail_wap_job",
    selection=dg.AssetSelection.all(),
)

daily_sales_job = dg.define_asset_job(
    name="retail_daily_sales_job",
    selection=dg.AssetSelection.assets("dlt_retail_sales_lines"),
)
hourly_inventory_job = dg.define_asset_job(
    name="retail_hourly_inventory_job",
    selection=dg.AssetSelection.assets("dlt_retail_inventory"),
)
weekly_reference_job = dg.define_asset_job(
    name="retail_weekly_reference_job",
    selection=dg.AssetSelection.assets(
        "dlt_retail_products",
        "dlt_retail_stores",
        "dlt_retail_promotions",
    ),
)
daily_transform_job = dg.define_asset_job(
    name="retail_daily_transform_job",
    selection=dg.AssetSelection.assets(
        "sales_facts",
        "daily_store_mart",
        "product_category_performance",
    ),
)

daily_sales_schedule = dg.ScheduleDefinition(
    job=daily_sales_job,
    cron_schedule="15 2 * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
hourly_inventory_schedule = dg.ScheduleDefinition(
    job=hourly_inventory_job,
    cron_schedule="0 * * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
weekly_reference_schedule = dg.ScheduleDefinition(
    job=weekly_reference_job,
    cron_schedule="0 3 * * 0",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
daily_transform_schedule = dg.ScheduleDefinition(
    job=daily_transform_job,
    cron_schedule="0 4 * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
weekly_full_reconciliation_schedule = dg.ScheduleDefinition(
    job=retail_wap_job,
    cron_schedule="0 5 * * 1",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
