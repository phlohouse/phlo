"""Schedules for the sales domain.

All schedules default to stopped so an example checkout never launches work on
its own; they document the intended automation cadences.
"""

import dagster as dg

sales_domain_daily_job = dg.define_asset_job(
    name="sales_domain_daily_job",
    selection=dg.AssetSelection.assets("dlt_sales_deals", "deal_pipeline"),
)

sales_domain_daily_schedule = dg.ScheduleDefinition(
    job=sales_domain_daily_job,
    cron_schedule="10 2 * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
