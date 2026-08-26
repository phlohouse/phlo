"""Schedules for daily claim arrival and ordered downstream execution."""

import dagster as dg

claims_wap_job = dg.define_asset_job(name="claims_wap_job", selection=dg.AssetSelection.all())
daily_arrival_job = dg.define_asset_job(
    name="claims_daily_arrival_job",
    selection=dg.AssetSelection.assets("dlt_claims"),
)
ordered_downstream_job = dg.define_asset_job(
    name="claims_ordered_downstream_job",
    selection=dg.AssetSelection.assets(
        "dlt_eligibility_periods",
        "dlt_providers",
        "stg_providers",
        "stg_eligibility_periods",
        "claims_latest",
        "claim_codes",
        "valid_claims",
        "provider_utilization_monthly",
        "claim_cost_summary",
    ),
)

daily_arrival_schedule = dg.ScheduleDefinition(
    job=daily_arrival_job,
    cron_schedule="10 2 * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
ordered_downstream_schedule = dg.ScheduleDefinition(
    job=ordered_downstream_job,
    cron_schedule="40 2 * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
monthly_reconciliation_schedule = dg.ScheduleDefinition(
    job=claims_wap_job,
    cron_schedule="0 4 1 * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
