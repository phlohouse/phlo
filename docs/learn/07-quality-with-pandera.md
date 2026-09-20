# Quality with Pandera

Quality checks turn an expectation into a decision that a run can record. This post explains the contract-first mindset, the difference between blocking and advisory checks, and how the tutorial asset shows its result in Dagster.

## The problem from first principles

A pipeline can run without producing a useful dataset. A source can send null identifiers, values outside an allowed range, duplicate keys, or timestamps that are too old. A process exit code only tells you that code completed. It does not tell you whether the data met the expectation.

A quality rule names the expectation. A schema says which columns and types a batch must have. A uniqueness rule says that a key identifies one row. An accepted-values rule says that a field belongs to a known set. A freshness rule says that the newest data is within an allowed age.

Not every anomaly deserves the same response. A missing required key should block publication. A small change in row count might warn an operator while keeping the result available for investigation. The decision should be explicit rather than hidden in an exception handler.

Severity is a communication tool as well as a control. A blocking failure tells the next stage not to treat the result as ready. A warning tells an operator to investigate while preserving the result for a consumer who can tolerate the condition. The important question is not whether a rule can fail, but what decision follows when it does.

## How Phlo approaches it

The `phlo-pandera` package provides Pandera schema validation and quality checks. An ingestion decorator can receive `validation_schema`, `quality_checks`, and `strict_validation`. A strict validation failure becomes a blocking asset check and fails the materialisation. Non-strict validation records a warning and permits the run to finish.

The provider-neutral rules API can query a materialised table. `not_null`, `unique`, `accepted_values`, `range_between`, and `freshness` describe common expectations. A quality asset can set `blocking=False` when a failed rule should remain visible without preventing completion.

Checks run at different boundaries. Pandera validates the incoming batch while the ingestion asset is preparing rows. Table rules inspect the materialised relation. dbt tests inspect a transformation asset. Dagster brings those results together so you can distinguish a rejected input from a failed downstream model.

Start with the smallest rule that protects a real consumer need. If a report groups by `event_id`, test that the identifier is present and unique. If a status drives a workflow, test its allowed values. If a service needs recent data, add freshness. A long list of generic checks can hide the one rule that should have blocked publication.

Quality is not a promise that every source value is perfect. It is a visible decision about what the project accepts, what it warns about, and what it refuses to publish. Revisit those decisions when a consumer changes, because the same rule can be too strict for exploration and too weak for a regulated output.

Quality is not a promise that every source value is perfect. It is a visible decision about what the project accepts, what it warns about, and what it refuses to publish. Revisit those decisions when a consumer changes, because the same rule can be too strict for exploration and too weak for a regulated output.

Thresholds are supported for quality checks that allow a proportion of failures. `warn_threshold` maps a small failure fraction to warning severity. Pandera schema contract failures remain blocking. Freshness settings can describe warning and error ages through the asset's freshness policy.

The normal path is Dagster. `phlo materialize` runs the asset and records the check result. The Dagster UI shows checks next to the asset and the run that produced them.

## Try it

The tutorial's schema lives in `workflows/schemas/csv.py`. For a table-level rule, reuse the exact declaration from [Add quality checks](../guides/add-quality-checks.md):

```python
import phlo
from phlo.quality_rules import accepted_values, not_null, unique


@phlo.quality.rules(
    table="raw.events",
    rules=[
        not_null("event_id"),
        unique("event_id"),
        accepted_values("status", ["open", "closed"]),
    ],
    blocking=False,
)
def events_quality():
    return None
```

Materialise the tutorial asset through Dagster:

```bash
phlo materialize dlt_events --partition 2025-01-15
```

Open `http://localhost:10006`, select the `dlt_events` asset, and open the **Checks** pane. A passing run shows a passed check. An invalid row shows a failed check with the check name and violation message.

For an ingestion contract, keep the schema and asset declaration from the guide together. Use `strict_validation=True` when a failure must prevent publication:

```python
@phlo.ingest.dlt(
    table_name="events",
    unique_key="event_id",
    group="events",
    validation_schema=EventSchema,
    strict_validation=False,
    quality_checks=[no_future_events],
)

def events_with_warning(partition_date: str):
    return load_events(partition_date)
```

This example is advisory because `strict_validation=False`. Choose the value that matches the consumer risk, then verify the resulting check in Dagster.

## Mental model to keep

- A schema protects the shape of the input.
- A quality rule protects a property of the result.
- Blocking means the run cannot be treated as successful.
- Warning means the run records an issue and can finish.
- Dagster is where the run and check evidence meet.

## Where this goes wrong

- **A check exists but is never visible.** Run the asset through Dagster rather than calling a provider directly.
- **A warning is used for a hard contract.** Set strict validation or blocking behaviour when consumers cannot use an invalid result.
- **A check targets the wrong table.** Confirm the fully qualified table name and the partition that the run materialised.
- **A freshness value is treated as a throughput promise.** It is an expectation to monitor, not a guarantee that a source will respond.
- **A rule fails without a useful diagnosis.** Open the asset check metadata and the failed step log together.

## Next

Continue with [Schema evolution and contracts](08-schema-evolution-and-contracts.md) to see how quality expectations change when columns change. Use [Add quality checks](../guides/add-quality-checks.md) for the full Pandera and rule configuration and [Quality checks](../reference/quality-checks.md) for supported parameters.
