# Assets, partitions, and schedules

Assets are the units Dagster runs, partitions let you process a known slice of data, and schedules and sensors remove manual repetition. After reading this page, you can choose an asset, select its partitions, and understand which Dagster feature starts the run.

## Assets and asset keys

An asset is an `AssetSpec` discovered from an installed provider. The specification carries an asset key, group, dependencies, checks, partition information, and provider metadata before the asset function runs.

DLT assets use keys such as `dlt_<table>`. dbt assets use the translated dbt model key, and the `DBT_NAMESPACED_ASSET_KEYS` setting controls whether project namespaces are included.

## Dependencies and the asset graph

Use `depends_on` in provider-neutral flow declarations when an asset depends on another asset. Use `ref` and `source` when a transformation refers to a logical relation.

dbt dependencies come from the manifest `depends_on.nodes` values. A dbt source can carry `phlo_asset_key` metadata, which Phlo uses when it creates lineage edges.

## Partitions

Partitioned assets use a daily `PartitionSpec` in the provider asset specification. dbt and DLT assets can expose daily partitions.

`phlo materialize` defaults an omitted partition to today's UTC date for a selected partitioned asset. Use a completed date with `--partition`, or pass `--no-default-partition` when you explicitly do not want the default.

## How a run happens

`phlo materialize` launches a Dagster asset run in the configured container backend. The Dagster UI can launch the same asset from the asset graph, and a provider decorator's `cron` value becomes a Dagster schedule through the definitions builder.

The `--no-contract-refresh` option skips the automatic schema contract refresh before materialisation. When WAP is enabled in `phlo.yaml`, `phlo materialize` requires one asset name and does not accept `--select`, because WAP promotes one isolated run at a time.

## Selecting several assets

The `--select` value is forwarded to Dagster's [asset selection syntax](https://docs.dagster.io/guides/build/assets/asset-selection-syntax). Dagster accepts `*`, `tag:<tag>`, an asset key, and graph clauses with optional `+` or `*` prefixes and suffixes for upstream and downstream depth.

```bash
phlo materialize --select "tag:provider=dlt"
phlo materialize --select "dlt_orders"
phlo materialize --select "*"
phlo materialize --select "+event_summary"
```

`+event_summary` selects `event_summary` and its upstream assets. The `--dry-run` option prints the Dagster command without launching a run. Do not use `--select` with WAP mode.

## Backfills

`phlo backfill` runs a partitioned asset across `--start-date` and `--end-date`, an explicit comma-separated `--partitions` list, or a saved run resumed with `--resume`. It launches one Dagster materialisation per partition and accepts `--parallel`, `--delay`, `--dry-run`, and `--json`.

WAP backfills serialise partitions through promotion because each branch is based on the current catalog snapshot. `phlo backfill --resume` continues after an interrupted run.

## Schedules and sensors

A provider decorator such as the DLT decorator can pass `cron` into the discovered asset specification. Phlo turns that value into a Dagster schedule. `phlo.schedule` is deprecated and creates no scheduler entry.

The WAP sensors promote an audited branch and clean up stale branches in Nessie, which supports refs and promotion. Their intervals are controlled by `PHLO_WAP_PROMOTION_INTERVAL_SECONDS` and `PHLO_WAP_CLEANUP_INTERVAL_SECONDS`.

The maintenance sensor evaluates table maintenance policy and triggers expiry or optimisation work. The failure alert sensor scans failed runs and sends configured alerts.

## Where to look next

Read [Write-audit-publish](write-audit-publish.md) for branch isolation, [Ingest data](../guides/ingest-data.md) for asset authoring, and [CLI](../reference/cli.md) for command options.
