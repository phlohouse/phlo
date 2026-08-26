"""Schedules for the WAP failure lab.

All schedules default to stopped so an example checkout never launches work
on its own; they document the intended automation cadences.
"""

import dagster as dg

daily_batch_ingestion_job = dg.define_asset_job(
    name="wap_lab_daily_batch_ingestion_job",
    selection=dg.AssetSelection.assets("dlt_sensor_batches"),
)
hourly_relaxed_feed_job = dg.define_asset_job(
    name="wap_lab_hourly_relaxed_feed_job",
    selection=dg.AssetSelection.assets("dlt_sensor_batches_relaxed"),
)
weekly_wap_reconciliation_job = dg.define_asset_job(
    name="wap_failure_lab_wap_job", selection=dg.AssetSelection.all()
)

hourly_relaxed_feed_schedule = dg.ScheduleDefinition(
    job=hourly_relaxed_feed_job,
    cron_schedule="10 * * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
daily_batch_ingestion_schedule = dg.ScheduleDefinition(
    job=daily_batch_ingestion_job,
    cron_schedule="30 2 * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
weekly_wap_reconciliation_schedule = dg.ScheduleDefinition(
    job=weekly_wap_reconciliation_job,
    cron_schedule="0 4 * * 1",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
