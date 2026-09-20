# Assets, partitions, and schedules

This page explains how Phlo turns asset declarations into Dagster runs, how partition keys affect those runs, and how schedules and sensors trigger work.

## Assets and asset keys

An asset is an `AssetSpec` discovered from an installed provider. The specification carries an asset key, group, dependencies, checks, partition information, and provider metadata before the asset function runs.

DLT assets use keys such as `dlt_<table>` in `packages/phlo-dlt/src/phlo_dlt/executor.py`. dbt assets use the translated dbt model key in `packages/phlo-dbt/src/phlo_dbt/assets.py`, and the `DBT_NAMESPACED_ASSET_KEYS` setting controls whether project namespaces are included.

## Dependencies and the asset graph

Use `depends_on` in provider-neutral flow declarations when an asset depends on another asset. Use `ref` and `source` when a transformation refers to a logical relation. The dependency normalisation and graph inputs are defined in `src/phlo/flow.py`.

dbt dependencies come from the manifest `depends_on.nodes` values in `packages/phlo-dbt/src/phlo_dbt/assets.py`. A dbt source can carry `phlo_asset_key` metadata, which the dbt asset builder uses when it creates lineage edges.

## Partitions

Partitioned assets use a daily `PartitionSpec` in the provider asset specification. The dbt builder creates daily partitions in `packages/phlo-dbt/src/phlo_dbt/assets.py`, and DLT scaffolding creates partitioned assets in `packages/phlo-dlt/src/phlo_dlt/scaffold.py`.

`phlo materialize` defaults an omitted partition to today's UTC date for a selected partitioned asset. Use a completed date with `--partition`, or pass `--no-default-partition` when you explicitly do not want the default, as implemented in `packages/phlo-dagster/src/phlo_dagster/cli_materialize.py`.

## How a run happens

`phlo materialize` launches a Dagster asset run in the configured container backend. The Dagster UI can launch the same asset from the asset graph, and a provider decorator's `cron` value becomes a Dagster schedule through the definitions builder.

The `--no-contract-refresh` option skips the automatic schema contract refresh before materialisation. When WAP is enabled in `phlo.yaml`, `phlo materialize` requires one asset name and does not accept `--select`, because WAP promotes one isolated run at a time.

## Selecting several assets

The `--select` value is passed unchanged to the Dagster command as its `--select` value by `packages/phlo-dagster/src/phlo_dagster/cli_materialize.py`. Dagster accepts `*`, `tag:<tag>`, an asset key, and graph clauses with optional `+` or `*` prefixes and suffixes for upstream and downstream depth.

```bash
phlo materialize --select "tag:bronze"
phlo materialize --select "dlt_orders"
phlo materialize --select "*"
```

These examples use Dagster selector forms. Confirm the selected asset set in a dry run before executing a broad selection, and do not use `--select` with WAP mode.

## Backfills

`phlo backfill` runs a partitioned asset across `--start-date` and `--end-date`, an explicit comma-separated `--partitions` list, or a saved run resumed with `--resume`. It launches one Dagster materialisation per partition and accepts `--parallel`, `--delay`, `--dry-run`, and `--json`.

WAP backfills serialise partitions through promotion because each branch is based on the current catalog snapshot. The implementation records progress so `phlo backfill --resume` can continue after an interrupted run.

## Schedules and sensors

A provider decorator such as the DLT decorator can pass `cron` into the discovered asset specification. `packages/phlo-dagster/src/phlo_dagster/framework/definitions.py` merges those schedules into Dagster definitions, while `phlo.schedule` in `src/phlo/flow.py` is deprecated and creates no scheduler entry.

The WAP sensors in `packages/phlo-dagster/src/phlo_dagster/wap_sensors.py` promote an audited branch and clean up stale branches when the selected catalog supports refs and promotion. Their intervals are controlled by `PHLO_WAP_PROMOTION_INTERVAL_SECONDS` and `PHLO_WAP_CLEANUP_INTERVAL_SECONDS`.

The maintenance sensor in `packages/phlo-dagster/src/phlo_dagster/maintenance_sensor.py` evaluates table maintenance policy and triggers expiry or optimisation work. The failure alert sensor in `packages/phlo-dagster/src/phlo_dagster/alerting_sensor.py` scans failed runs and sends configured alerts.

## Where to look next

Read [Write-audit-publish](write-audit-publish.md) for branch isolation, [Ingest data](../guides/ingest-data.md) for asset authoring, and [CLI](../reference/cli.md) for exact options.
