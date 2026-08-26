"""Schedules for hourly ingestion, rolling late-data repair, and fleet builds.

All schedules default to stopped so an example checkout never launches work
on its own; they document the intended automation cadences.
"""

import dagster as dg

iot_telemetry_wap_job = dg.define_asset_job(
    name="iot_telemetry_wap_job", selection=dg.AssetSelection.all()
)
hourly_ingestion_job = dg.define_asset_job(
    name="iot_telemetry_hourly_ingestion_job",
    selection=dg.AssetSelection.assets("dlt_telemetry_readings"),
)
rolling_repair_job = dg.define_asset_job(
    name="iot_telemetry_rolling_repair_job",
    selection=dg.AssetSelection.assets(
        "dlt_telemetry_corrections",
        "telemetry_dedup",
        "device_health_hourly",
        "device_health_current",
    ),
)
daily_fleet_job = dg.define_asset_job(
    name="iot_telemetry_daily_fleet_job",
    selection=dg.AssetSelection.assets(
        "dlt_device_registry",
        "dlt_site_directory",
        "fleet_daily_summary",
        "site_daily_report",
    ),
)

hourly_ingestion_schedule = dg.ScheduleDefinition(
    job=hourly_ingestion_job,
    cron_schedule="20 * * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
rolling_repair_schedule = dg.ScheduleDefinition(
    job=rolling_repair_job,
    cron_schedule="40 * * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
daily_fleet_schedule = dg.ScheduleDefinition(
    job=daily_fleet_job,
    cron_schedule="15 1 * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
weekly_reconciliation_schedule = dg.ScheduleDefinition(
    job=iot_telemetry_wap_job,
    cron_schedule="0 3 * * 1",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
