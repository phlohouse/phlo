"""Schedules for the operations domain.

All schedules default to stopped so an example checkout never launches work on
its own; they document the intended automation cadences.
"""

import dagster as dg

operations_domain_daily_job = dg.define_asset_job(
    name="operations_domain_daily_job",
    selection=dg.AssetSelection.assets("dlt_operations_incidents", "incident_summary"),
)

operations_domain_daily_schedule = dg.ScheduleDefinition(
    job=operations_domain_daily_job,
    cron_schedule="40 2 * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
