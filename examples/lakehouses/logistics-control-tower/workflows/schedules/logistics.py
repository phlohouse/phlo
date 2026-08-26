"""Schedules for the logistics control tower.

All schedules default to stopped so an example checkout never launches work on
its own; they document the intended automation cadences. The two carrier feeds
deliberately poll at different cadences: ATLAS publishes hourly scans while
CORSAIR batches every four hours.
"""

import dagster as dg

control_tower_wap_job = dg.define_asset_job(
    name="logistics_control_tower_wap_job", selection=dg.AssetSelection.all()
)

orders_incremental_job = dg.define_asset_job(
    name="logistics_orders_incremental_job",
    selection=dg.AssetSelection.assets("sling_shipments_orders"),
)

atlas_polling_job = dg.define_asset_job(
    name="logistics_atlas_polling_job",
    selection=dg.AssetSelection.assets("dlt_carrier_events_atlas"),
)

corsair_polling_job = dg.define_asset_job(
    name="logistics_corsair_polling_job",
    selection=dg.AssetSelection.assets("dlt_carrier_events_corsair"),
)

reference_refresh_job = dg.define_asset_job(
    name="logistics_reference_refresh_job",
    selection=dg.AssetSelection.assets("dlt_carrier_directory", "dlt_sla_terms"),
)

daily_marts_job = dg.define_asset_job(
    name="logistics_daily_marts_job",
    selection=dg.AssetSelection.assets(
        "order_current_state",
        "carrier_events_unified",
        "shipment_exceptions",
        "carrier_coverage",
        "warehouse_dwell",
        "warehouse_scan_exceptions",
        "control_tower_shipment_grid",
        "canonical_shipment_state",
        "transit_duration",
        "sla_mart",
    ),
)

orders_incremental_schedule = dg.ScheduleDefinition(
    job=orders_incremental_job,
    cron_schedule="*/20 * * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

atlas_polling_schedule = dg.ScheduleDefinition(
    job=atlas_polling_job,
    cron_schedule="10 * * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

corsair_polling_schedule = dg.ScheduleDefinition(
    job=corsair_polling_job,
    cron_schedule="35 */4 * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

reference_refresh_schedule = dg.ScheduleDefinition(
    job=reference_refresh_job,
    cron_schedule="15 2 * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

daily_marts_schedule = dg.ScheduleDefinition(
    job=daily_marts_job,
    cron_schedule="40 2 * * *",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

weekly_reconciliation_schedule = dg.ScheduleDefinition(
    job=control_tower_wap_job,
    cron_schedule="0 3 * * 1",
    execution_timezone="UTC",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
