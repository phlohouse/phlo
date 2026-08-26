"""Coordinated WAP schedule spanning every federated domain.

All schedules default to stopped so an example checkout never launches work on
its own; they document the intended automation cadences.

FEDERATION NOTE: the weekly job selects every registered asset, but dbt
materializations only ever cover the single active project's models, so
"coordinated WAP" across domains is currently bounded by the same
single-active-project limitation recorded in FEDERATION_FINDINGS.md.
"""

import dagster as dg

federated_domains_wap_job = dg.define_asset_job(
    name="federated_domains_wap_job",
    selection=dg.AssetSelection.all(),
)

federated_domains_weekly_schedule = dg.ScheduleDefinition(
    job=federated_domains_wap_job,
    cron_schedule="0 3 * * 1",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
