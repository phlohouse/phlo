"""Schedules for the finance domain.

All schedules default to stopped so an example checkout never launches work on
its own; they document the intended automation cadences.
"""

import dagster as dg

finance_domain_daily_job = dg.define_asset_job(
    name="finance_domain_daily_job",
    selection=dg.AssetSelection.assets("dlt_finance_invoices", "invoice_aging"),
)

finance_domain_daily_schedule = dg.ScheduleDefinition(
    job=finance_domain_daily_job,
    cron_schedule="25 2 * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
