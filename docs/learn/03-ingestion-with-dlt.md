# Ingestion with dlt

Ingestion is the boundary where an external source becomes a table that your platform can replay and check. This post explains extraction, normalisation, loading, partitions, and merge behaviour, then uses the tutorial asset to show what those decisions look like in Phlo.

## The problem from first principles

An ingestion job has three jobs that are easy to confuse. It must extract records from a source, normalise them into a usable shape, and load them into storage. A source can succeed at extraction while still producing rows that cannot be checked or merged safely.

Overwriting a table on every run is tempting because it is easy to describe. It becomes risky when a source sends late records, repeats records, or fails halfway through a range. A staging and merge approach gives the run a place to prepare rows before it changes the consumer-facing table. The unique key tells the system which rows represent the same entity.

Partitions give the run a bounded unit of work. A daily partition can be retried without replaying the entire source. It also gives you a natural freshness question: which day is missing or late? Reference data that has no meaningful time slice can be unpartitioned instead.

Staging also protects the reader from partial work. The run can collect and validate its rows before the table state changes. If the run fails while reading the source, the previous published state remains available. If the run succeeds but a later check blocks publication, you have an audit result to investigate instead of an unexplained overwrite.

Replayability is the practical benefit of these choices. A dated partition, stable key, and explicit merge strategy let you answer what to rerun and what a rerun should do. Without those decisions, retrying an ingestion job can create a second copy of a record or replace a complete table with an incomplete response.

## How Phlo approaches it

The `phlo-dlt` package provides `@phlo.ingest.dlt`. The decorator turns a Python function into a Dagster asset and connects the returned dlt resource to table storage. `phlo-pandera` can validate each batch before publication.

The tutorial asset uses a daily partition and `unique_key="event_id"`. The default `merge_strategy="merge"` upserts rows on that key. The other supported value is `merge_strategy="append"`, which inserts rows without using a merge key. Use append for an immutable event stream where each row is new. Set `partitioned=False` for reference data that has no natural partition.

Phlo accepts `merge_strategy="append"` and `merge_strategy="merge"`. These are the only values the ingestion decorator accepts. `freshness_hours=(warning_hours, error_hours)` can add freshness checks, while `strict_validation` controls whether failed validation blocks the run.

The decorator does not decide what a row means for you. You still need to choose a key that identifies the source entity and a partition that matches the source's time semantics. If the source has immutable events, an event identifier can be retained as data while append preserves each event. If the source sends current entity state, a stable key and merge make replaying a partition safer.

The normal run path is Dagster. `phlo materialize` launches one asset run, and `phlo backfill` launches runs across several partitions. `phlo sling run` performs a Sling replication directly for debugging or inspection. It does not create the normal Dagster run record, lineage, or asset-check results.

## Try it

Open `workflows/ingestion/csv/events.py` in the tutorial project. Its core asset is:

```python
@phlo.ingest.dlt(
    table_name="events",
    unique_key="event_id",
    validation_schema=EventsSchema,
    group="csv",
    freshness_hours=(1, 24),
)
def csv_events(partition_date: str) -> object:
    events = pd.read_csv(Path("data/events.csv"))
    events["event_id"] = events["id"].astype(str) + "-" + partition_date
    rows = events.to_dict(orient="records")
    return dlt.resource(rows, name="events")
```

Materialise one completed partition through Dagster:

```bash
phlo materialize dlt_events --partition 2025-01-15
```

Inspect the resulting tables:

```bash
phlo catalog tables
```

Load a range of completed partitions with one Dagster run per partition:

```bash
phlo backfill dlt_events --start-date 2025-01-01 --end-date 2025-01-14
```

Use `--parallel` when the source and storage can handle concurrent work:

```bash
phlo backfill dlt_events --start-date 2025-01-01 --end-date 2025-01-14 --parallel 4
```

The backfill command also accepts `--partitions`, `--resume`, `--dry-run`, `--delay`, and `--json`. Check the command reference when you need one of those options.

## Mental model to keep

- Extracting records is not the same as validating or publishing them.
- A partition is a replay and troubleshooting boundary.
- A unique key describes identity, not merely a database index.
- Merge protects replayable entities, while append fits immutable events.
- Dagster records the normal ingestion run.

## Where this goes wrong

- **A source repeats rows.** A merge strategy and unique key determine whether a replay updates one entity or creates duplicates.
- **A partition is missing.** Dagster partition status and a backfill identify the exact date to rerun.
- **Reference data is forced into daily partitions.** `partitioned=False` removes a time boundary that does not represent the source.
- **Validation fails before the table changes.** Pandera reports the field or rule that rejected the batch.
- **A provider command appears successful but the run history is empty.** Direct provider commands bypass Dagster, so use `phlo materialize` for the normal path.

## Next

Continue with [Tables with Iceberg and Nessie](04-tables-with-iceberg-and-nessie.md) to see how the loaded rows become versioned table state. For implementation detail, read [Ingest data](../guides/ingest-data.md), and for checks read [Add quality checks](../guides/add-quality-checks.md).
