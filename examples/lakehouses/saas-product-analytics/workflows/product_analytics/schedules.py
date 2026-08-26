"""Schedules reflect hourly API cursors and a daily cohort rebuild."""

import dagster as dg

product_analytics_wap_job = dg.define_asset_job(
    name="product_analytics_wap_job", selection=dg.AssetSelection.all()
)
hourly_events_job = dg.define_asset_job(
    name="product_analytics_hourly_events_job",
    selection=dg.AssetSelection.assets("dlt_saas_events"),
)
daily_cohorts_job = dg.define_asset_job(
    name="product_analytics_daily_cohorts_job",
    selection=dg.AssetSelection.assets(
        "activation", "retention", "feature_adoption", "release_impact"
    ),
)

hourly_events_schedule = dg.ScheduleDefinition(
    job=hourly_events_job,
    cron_schedule="10 * * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
daily_cohorts_schedule = dg.ScheduleDefinition(
    job=daily_cohorts_job,
    cron_schedule="30 2 * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
weekly_publication_schedule = dg.ScheduleDefinition(
    job=product_analytics_wap_job,
    cron_schedule="0 4 * * 1",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
