"""Schedules for weekday market ingestion and calendar-aware rebuilds."""

import dagster as dg

markets_wap_job = dg.define_asset_job(name="markets_wap_job", selection=dg.AssetSelection.all())
weekday_market_ingestion_job = dg.define_asset_job(
    name="markets_weekday_ingestion_job",
    selection=dg.AssetSelection.assets("dlt_equities_bars", "dlt_fx_rates"),
)
reference_refresh_job = dg.define_asset_job(
    name="markets_reference_refresh_job",
    selection=dg.AssetSelection.assets(
        "dlt_security_master",
        "dlt_trading_calendar",
        "dlt_portfolio_holdings",
    ),
)
analytics_rebuild_job = dg.define_asset_job(
    name="markets_analytics_rebuild_job",
    selection=dg.AssetSelection.assets(
        "stg_securities",
        "stg_calendar",
        "prices_normalized",
        "daily_returns",
        "rolling_volatility",
        "drawdown",
        "fx_cross_check",
        "portfolio_exposure",
    ),
)

weekday_market_ingestion_schedule = dg.ScheduleDefinition(
    job=weekday_market_ingestion_job,
    cron_schedule="30 5 * * 1-5",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
reference_refresh_schedule = dg.ScheduleDefinition(
    job=reference_refresh_job,
    cron_schedule="0 3 * * 1-5",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
analytics_rebuild_schedule = dg.ScheduleDefinition(
    job=analytics_rebuild_job,
    cron_schedule="45 5 * * 1-5",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
weekly_reconciliation_schedule = dg.ScheduleDefinition(
    job=markets_wap_job,
    cron_schedule="0 6 * * 6",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
