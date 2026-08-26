"""Jobs and schedules mirroring each stream's source cadence.

Order and payment incrementals run frequently because the source mutates
constantly; reference tables refresh nightly; the customer snapshot is weekly;
transforms and the full WAP reconciliation run after the data lands.
"""

import dagster as dg

commerce_wap_job = dg.define_asset_job(
    name="commerce_wap_job",
    selection=dg.AssetSelection.all(),
)

frequent_incremental_job = dg.define_asset_job(
    name="commerce_frequent_incremental_job",
    selection=dg.AssetSelection.assets(
        "sling_commerce_orders",
        "sling_commerce_order_lines",
        "sling_commerce_payments",
    ),
)
nightly_reference_job = dg.define_asset_job(
    name="commerce_nightly_reference_job",
    selection=dg.AssetSelection.assets(
        "sling_commerce_products",
        "sling_commerce_config",
    ),
)
weekly_customer_snapshot_job = dg.define_asset_job(
    name="commerce_weekly_customer_snapshot_job",
    selection=dg.AssetSelection.assets("sling_commerce_customers"),
)
daily_transform_job = dg.define_asset_job(
    name="commerce_daily_transform_job",
    selection=dg.AssetSelection.assets(
        "customer_dimension",
        "order_lifecycle_facts",
        "daily_revenue_mart",
        "payment_reconciliation",
    ),
)

frequent_incremental_schedule = dg.ScheduleDefinition(
    job=frequent_incremental_job,
    cron_schedule="*/15 * * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
nightly_reference_schedule = dg.ScheduleDefinition(
    job=nightly_reference_job,
    cron_schedule="30 2 * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
weekly_customer_snapshot_schedule = dg.ScheduleDefinition(
    job=weekly_customer_snapshot_job,
    cron_schedule="0 3 * * 6",
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
    job=commerce_wap_job,
    cron_schedule="0 5 * * 1",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
