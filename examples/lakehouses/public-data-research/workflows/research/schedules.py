"""Stopped Dagster schedules for public-data research ingestion and rebuilds.

Each source keeps its own natural cadence: civic daily, weather monthly,
demographics annually, with a daily research rebuild and a weekly full WAP
reconciliation. Every schedule registers stopped - operators opt in.
"""

import dagster as dg

public_data_research_wap_job = dg.define_asset_job(
    name="public_data_research_wap_job", selection=dg.AssetSelection.all()
)
civic_daily_ingestion_job = dg.define_asset_job(
    name="research_civic_daily_ingestion_job",
    selection=dg.AssetSelection.assets("dlt_places_registry"),
)
geo_refresh_job = dg.define_asset_job(
    name="research_geo_refresh_job",
    selection=dg.AssetSelection.assets("dlt_places_geo"),
)
weather_monthly_ingestion_job = dg.define_asset_job(
    name="research_weather_monthly_ingestion_job",
    selection=dg.AssetSelection.assets("dlt_weather_observations"),
)
demographics_annual_ingestion_job = dg.define_asset_job(
    name="research_demographics_annual_ingestion_job",
    selection=dg.AssetSelection.assets("dlt_region_demographics"),
)
research_rebuild_job = dg.define_asset_job(
    name="research_analytics_rebuild_job",
    selection=dg.AssetSelection.assets(
        "stg_observations",
        "places",
        "monthly_indicators",
        "annual_rollup",
    ),
)

civic_daily_ingestion_schedule = dg.ScheduleDefinition(
    job=civic_daily_ingestion_job,
    cron_schedule="15 6 * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
weather_monthly_ingestion_schedule = dg.ScheduleDefinition(
    job=weather_monthly_ingestion_job,
    cron_schedule="0 7 2 * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
demographics_annual_ingestion_schedule = dg.ScheduleDefinition(
    job=demographics_annual_ingestion_job,
    cron_schedule="0 8 1 2 *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
research_rebuild_schedule = dg.ScheduleDefinition(
    job=research_rebuild_job,
    cron_schedule="45 7 * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
weekly_reconciliation_schedule = dg.ScheduleDefinition(
    job=public_data_research_wap_job,
    cron_schedule="0 6 * * 6",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
