# Ingest bounded data intervals

Use interval ingestion to load adjacent time windows, recover late arrivals, and replay history without moving a live extraction cursor.
The [complete order-ingestion example](../../examples/data_intervals.py) runs through Phlo's provider discovery, Dagster adapter, DLT, and Iceberg.

## Before you start

Use a source with timezone-aware timestamps and a stable, non-null unique key.
Interval ingestion requires `merge_strategy="merge"`, which is the default.
Append and history writes are not supported in interval mode.
Return one DLT resource, not a multi-resource DLT source or a plain list.

Intervals use an inclusive start and an exclusive end, written `[start, end)`.
They describe extraction windows, not Iceberg partition transforms.
Keep physical table partitioning in `partition_spec`.

## Define the source

Set `interval_column` on `@phlo.ingest.dlt` and accept `data_interval` instead of `partition_date`.
Return timestamps as aware Python `datetime` values or timestamp values DLT can convert.
Use `data_interval.start` and `data_interval.end` as query parameters, with `>=` and `<` respectively.
The complete example uses a parameterised SQLite query and declares an Iceberg schema.

```python
@phlo.ingest.dlt(
	table_name="orders",
	unique_key="id",
	group="raw",
	table_schema=order_schema,
	interval_column="updated_at",
	interval_lookback=timedelta(hours=2),
	merge_strategy="merge",
)
def orders(data_interval: DataInterval):
	return bounded_order_resource(data_interval.start, data_interval.end)
```

Import `DataInterval` from `phlo.capabilities` and `timedelta` from `datetime`.
Supply your schema and resource, or use the complete runnable example.
DLT applies the same bounds before Parquet staging, even if your source does not push them into its query.
Without query pushdown, the source can still read its entire history. The framework does not infer arbitrary API or SQL filtering.

Existing sources that accept `partition_date` need no changes unless you opt into interval ingestion.

## Select scheduled execution or replay

The Dagster adapter translates its time-window partitions into `runtime.routing.data_interval`.
Endpoints are UTC instants. Daily calendar windows retain their timezone rules, including 23-hour and 25-hour Europe/London days.
`PartitionSpec.start_date` and `PartitionSpec.timezone` control translated daily partitions. Existing ingestion assets retain their Europe/London default.
Non-time partitions do not supply a data interval. Interval ingestion fails before calling the source when no interval is available.

- Scheduled execution is the default. Phlo subtracts `interval_lookback` from the start in elapsed time and keeps the end unchanged.
- Set the run tag `phlo/interval_mode=replay` for an exact historical window. Replay does not apply lookback.
- Dagster backfills with the `dagster/backfill` tag select replay automatically.
- A manual historical partition run still uses scheduled semantics unless you select replay explicitly.

For other orchestrators, supply `RuntimeRouting(data_interval=DataInterval(start, end), interval_mode="replay")` on your runtime context.
Naive, empty, and reversed intervals are rejected. Unknown interval modes are rejected rather than silently treated as scheduled execution.

## Run the lakehouse example

From a full repository checkout, run `make setup`.
Configure running Nessie and MinIO services as described in [your first pipeline](../getting-started/first-pipeline.md).
The example writes a uniquely named `raw.interval_orders_*` table on the configured ref and retains it for inspection.
Use a disposable development lakehouse, not a production catalogue.

Run from the repository root with your lakehouse connection environment:

```bash
PHLO_OBSERVATORY_SETTINGS_BACKEND=memory uv run --locked python examples/data_intervals.py
```

The explicit in-memory setting is for development only. Omit it when you have configured the normal PostgreSQL settings store.
The example runs two adjacent London days across the spring clock change, adds a late arrival, then replays the first day.
Its decisive output is:

```text
EXTRACT [2026-03-28T22:00:00+00:00, 2026-03-29T23:00:00+00:00) ids=[101]
DESTINATION 2026-03-29 scheduled ids=[101]
EXTRACT [2026-03-29T21:00:00+00:00, 2026-03-30T23:00:00+00:00) ids=[202, 404]
DESTINATION 2026-03-30 scheduled ids=[101, 202, 404]
EXTRACT [2026-03-29T00:00:00+00:00, 2026-03-29T23:00:00+00:00) ids=[101, 404]
DESTINATION 2026-03-29 replay ids=[101, 202, 404]
```

Record `202` is exactly on the shared boundary. It belongs to the second interval, so no record disappears between exclusive watermark predicates.
Record `404` arrives late but falls inside the two-hour lookback. Merge writes retain one row per ID after replay.
Record `303` is on the second interval's exclusive end and is not extracted.

## Retry without claiming cursor atomicity

Interval ingestion sets DLT's `initial_value`, `end_value`, and closed-start/open-end options before extraction.
DLT's bounded incremental mode is stateless: it neither consults nor advances the persisted incremental cursor for that resource.
Both scheduled interval runs and replays preserve an existing live cursor. Custom source code must not update its own progress state.

The scheduler records successful materialisations. There is no additional interval checkpoint store or global contiguous high-water mark.
DLT staging completion is not evidence of a published table write.
A failed or uncertain table write remains a failed run. Retry the same interval after inspecting the destination when necessary.
A commit whose reply is lost can already have changed the table; merge makes a retry idempotent for stable unique keys.
Multiple staged files can still involve multiple provider commits. Interval mode does not add an atomic batch transaction.

This contract does not make an external cursor and a table commit atomic.
Legacy unbounded DLT sources retain their existing provider-managed state behaviour, which can advance during staging before table publication.
Kafka retains its existing snapshot-bound ingestion checkpoint protocol. Time intervals do not reinterpret stream offsets.
`mark_watermark_committed` remains an immutable dictionary helper, not a durable commit protocol.
