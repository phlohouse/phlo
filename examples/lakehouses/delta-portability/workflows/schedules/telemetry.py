"""Schedules for hourly ingestion, rolling repair, reference refresh, and a
non-WAP weekly reconciliation pass.

All schedules default to stopped so an example checkout never launches work
on its own. There is no WAP job: Delta Lake does not support catalog branches
(``supports_refs=false``), so the weekly pass is an ordinary full rebuild over
main rather than a branch-first launch.
"""

import dagster as dg

hourly_ingestion_job = dg.define_asset_job(
    name="delta_portability_hourly_ingestion_job",
    selection=dg.AssetSelection.assets("dlt_telemetry_readings"),
)
rolling_repair_job = dg.define_asset_job(
    name="delta_portability_rolling_repair_job",
    selection=dg.AssetSelection.assets(
        "dlt_telemetry_corrections",
        "telemetry_dedup",
        "device_health_hourly",
        "device_health_current",
    ),
)
daily_reference_job = dg.define_asset_job(
    name="delta_portability_daily_reference_job",
    selection=dg.AssetSelection.assets(
        "sling_delta_regions_snapshot",
        "dlt_delta_regions",
        "dlt_device_registry",
        "dlt_site_directory",
        "fleet_daily_summary",
        "site_daily_report",
    ),
)
weekly_reconciliation_job = dg.define_asset_job(
    name="delta_portability_weekly_reconciliation_job",
    selection=dg.AssetSelection.all(),
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
daily_reference_schedule = dg.ScheduleDefinition(
    job=daily_reference_job,
    cron_schedule="15 1 * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
weekly_reconciliation_schedule = dg.ScheduleDefinition(
    job=weekly_reconciliation_job,
    cron_schedule="0 3 * * 1",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
