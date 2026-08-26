"""Schedules for micro-batch ingestion, hourly mart refresh, and metadata.

All schedules default to stopped so an example checkout never launches work
on its own; they document the intended automation cadences. There is no WAP
reconciliation job because the ClickHouse data plane has no branch isolation
to reconcile (wap.enabled is false in phlo.yaml).
"""

import dagster as dg

micro_batch_ingestion_job = dg.define_asset_job(
    name="clickhouse_ops_micro_batch_job",
    selection=dg.AssetSelection.assets(
        "dlt_platform_events",
        "dlt_access_logs",
    ),
)
hourly_mart_refresh_job = dg.define_asset_job(
    name="clickhouse_ops_hourly_mart_refresh_job",
    selection=dg.AssetSelection.assets(
        "error_rate_hourly",
        "latency_p95_hourly",
        "throughput_hourly",
        "tenant_usage_daily",
    ),
)
nightly_metadata_job = dg.define_asset_job(
    name="clickhouse_ops_nightly_metadata_job",
    selection=dg.AssetSelection.assets(
        "sling_chmeta_tenants",
    ),
)

micro_batch_schedule = dg.ScheduleDefinition(
    job=micro_batch_ingestion_job,
    cron_schedule="*/15 * * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
hourly_mart_refresh_schedule = dg.ScheduleDefinition(
    job=hourly_mart_refresh_job,
    cron_schedule="10 * * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
nightly_metadata_schedule = dg.ScheduleDefinition(
    job=nightly_metadata_job,
    cron_schedule="30 2 * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
