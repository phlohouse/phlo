"""Schedules for the Customer 360 lakehouse; every schedule registers STOPPED."""

import dagster as dg

customer360_wap_job = dg.define_asset_job(
    name="customer360_wap_job", selection=dg.AssetSelection.all()
)

commerce_incremental_job = dg.define_asset_job(
    name="commerce_incremental_job",
    selection=dg.AssetSelection.assets("sling_c360_customers", "sling_c360_orders"),
)
support_marketing_ingestion_job = dg.define_asset_job(
    name="support_marketing_ingestion_job",
    selection=dg.AssetSelection.assets(
        "dlt_support_tickets",
        "dlt_marketing_contacts",
        "dlt_consent_events",
    ),
)
identity_rebuild_job = dg.define_asset_job(
    name="customer360_identity_rebuild_job",
    selection=dg.AssetSelection.assets(
        "stg_commerce_customers",
        "stg_commerce_orders",
        "stg_support_tickets",
        "stg_marketing_contacts",
        "stg_consent_events",
        "identity_resolution",
        "customer_dimension",
    ),
)
publication_job = dg.define_asset_job(
    name="customer360_publication_job",
    selection=dg.AssetSelection.assets("consent_current", "consent_safe_product"),
)

commerce_incremental_schedule = dg.ScheduleDefinition(
    job=commerce_incremental_job,
    cron_schedule="*/20 * * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
support_marketing_schedule = dg.ScheduleDefinition(
    job=support_marketing_ingestion_job,
    cron_schedule="15 * * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
identity_rebuild_schedule = dg.ScheduleDefinition(
    job=identity_rebuild_job,
    cron_schedule="30 2 * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
publication_schedule = dg.ScheduleDefinition(
    job=publication_job,
    cron_schedule="45 2 * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
weekly_reconciliation_schedule = dg.ScheduleDefinition(
    job=customer360_wap_job,
    cron_schedule="0 4 * * 6",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
