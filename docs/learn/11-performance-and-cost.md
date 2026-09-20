# Performance and cost

Performance work is most useful when it improves a result that is already correct and observable. This post gives you an order for optimisation, explains the main Phlo levers, and shows how to compare changes without inventing throughput or cost promises.

## The problem from first principles

Fast but incorrect data has negative value. Cheap but invisible data is difficult to trust. A performance change that removes checks or evidence can make the system appear better while making failures harder to detect.

Use this priority order:

1. Make the result correct.
2. Make the result observable.
3. Make the work fast.
4. Make the work cheap.

The order is not a claim that cost never matters. It is a way to avoid optimising the wrong thing. Measure the current run, identify the largest contributor, change one lever, and compare the result with the same workload.

The measurement boundary should match the change. A partition change needs a like-for-like partition range. A query change needs the same relation and result requirement. A parallelism change needs the same source and service limits. Dagster run duration tells you about orchestration and asset work, while a Trino query plan tells you about a particular query.

## How Phlo approaches it

Partitions are the first design lever. A partition should bound the source query, table write, and recovery unit. A daily partition makes a missed date easy to identify. An unpartitioned reference asset avoids pretending that a time window exists when it does not.

Backfill parallelism is the second lever. `phlo backfill --parallel` can process several partitions concurrently, but the source, object store, catalogue, and WAP promotion path still have limits. The command defaults to one concurrent partition, and WAP runs serialise through promotion. `--delay` adds rate limiting between parallel executions.

Merge strategy affects write work. `merge_strategy="merge"` protects entity identity through a unique key. `merge_strategy="append"` avoids merge work for immutable event streams. Choose based on the source semantics, not only on benchmark speed.

Iceberg maintenance can compact files or expire snapshots. The plan-first operations command creates a read-only plan for `compact` or `snapshot_expiry`. Review the plan and safety limits before applying the operation. Trino query hygiene also matters. Read only the columns you need and filter on partition fields so the engine can prune work.

Dagster run durations, asset status, and metrics provide the comparison surface. Avoid publishing a fixed timing or throughput number because the result depends on source size, partition design, services, and storage.

Cost also includes operator time and recovery time. A design that saves a small amount of storage but makes failures opaque can cost more during an incident. A larger table with clear partitions and checks can be cheaper to operate than a smaller table that requires manual reconstruction.

An optimisation is complete only when the new behaviour is documented. Record which partition or query changed, which Dagster run provides the comparison, and which checks still pass. Future operators then have a baseline and can tell whether a later slowdown comes from data growth, a service change, or an accidental regression.

The same discipline applies to maintenance. A compaction plan is useful when it explains why files have become fragmented and what table it affects. Snapshot expiry is useful when retention is understood. Keep the plan with the operational change so a later incident can distinguish an intentional maintenance result from a failed write.

## Try it

Record the current asset run and inspect its duration in Dagster. Then inspect the asset and service state:

```bash
phlo status --assets
phlo status --services
```

Preview the work for a backfill:

```bash
phlo backfill dlt_events --start-date 2025-01-01 --end-date 2025-01-14 --parallel 4 --dry-run
```

Create a read-only maintenance plan for a table:

```bash
phlo operations maintenance plan --operation compact --table raw.events
```

The plan records the table and operation without mutating it. Review the plan before applying any maintenance action. Maintenance changes table state and should be performed during an approved window with a recovery path.

Query only the columns needed for a comparison:

```bash
phlo trino --catalog iceberg
```

```sql
SELECT event_id, value FROM raw.events WHERE _phlo_partition_date = '2025-01-15';
```

Run the same partition after one change and compare the Dagster duration, row count, check result, and query behaviour. If the result is not correct, revert the change before pursuing speed.

## Mental model to keep

- Correctness is the first performance requirement.
- A partition bounds both work and recovery.
- Parallelism moves pressure to the source and storage.
- Merge and append represent different data semantics.
- Maintenance changes storage state and needs a plan.
- Compare like with like and record what changed.

## Where this goes wrong

- **Parallelism hides a source limit.** Lower `--parallel` or add `--delay` when source errors or throttling increase.
- **Append is used for mutable entities.** Duplicate keys reveal that the source needs merge semantics.
- **A query scans the whole table.** Filter by the partition column and select only required columns.
- **Maintenance is applied without a plan.** The plan command reveals the intended operation and safety limits before mutation.
- **A benchmark uses a different workload.** Compare the same asset, partition range, checks, and service state.

## Next

Continue with [Extending Phlo](12-extending-phlo.md) to see how capability boundaries let you add a provider without rewriting the core. Use [Maintain and recover](../guides/maintain-and-recover.md) for guarded maintenance actions and [Assets, partitions, and schedules](../concepts/assets-partitions-and-schedules.md) for partition design.
