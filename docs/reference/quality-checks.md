# Quality checks reference

Quality checks return `QualityCheckResult` values with a pass state, metric name, metric value, metadata, and an optional failure message. A check can be blocking or advisory through its surrounding provider contract.

## NullCheck

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `columns` | `list[str]` | required | Columns checked for null values. |
| `allow_threshold` | `float` | `0.0` | Maximum null fraction. |

## RangeCheck

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `column` | `str` | required | Numeric column. |
| `min_value` | `float \| None` | `None` | Inclusive lower bound. |
| `max_value` | `float \| None` | `None` | Inclusive upper bound. |
| `allow_threshold` | `float` | `0.0` | Maximum violating fraction. |

## FreshnessCheck

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `timestamp_column` | `str` | required | Timestamp column. |
| `max_age_hours` | `float` | required | Maximum age. |
| `reference_time` | `datetime \| None` | `None` | Reference time for evaluation. |
| `require_timestamps` | `bool` | `False` | Fail when no usable timestamp exists, including an empty query result. Interpret naive data timestamps as UTC. |

Freshness compares the newest parseable timestamp with the reference time.
The age limit is inclusive. Without a reference, the check uses the current
clock. Legacy checks permit a column containing no usable timestamps.
Missing columns fail in both modes.

Explicit checks report `freshness_check_<column>` as their metric name when
`require_timestamps=True`. Legacy checks retain `freshness_check`. Result
metadata includes the timestamp column, reference mode, reference time,
threshold, newest timestamp, and observed age. Unavailable timestamps produce
`missing_timestamp_column` or `no_usable_timestamps` reasons.

### SLA data freshness

`phlo.SLA` accepts two optional keyword-only fields:

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `freshness_column` | `str \| None` | `None` | Data timestamp whose newest value defines freshness. |
| `freshness_reference_time` | `datetime \| None` | `None` | Timezone-aware evaluation reference for historical runs. |

An explicit column requires non-negative `freshness_hours`. A reference also
requires a column. Blank columns and timezone-naive references raise `ValueError`.
Omitting the reference uses the current clock. Explicit SLA checks convert data
timestamps to UTC, including timestamps with different offsets. Naive data
timestamps mean UTC, not the machine's local timezone.

`@phlo.quality.pandera(..., sla=promise)` generates and executes the explicit
freshness check alongside the supplied checks. An identical check is not added
twice. A conflicting same-column check raises `ValueError`. Legacy SLAs without
`freshness_column` remain metadata-only on this decorator.

`freshness_rule_from_sla` and `freshness_check_from_sla` preserve the column and
reference. Their column overrides cannot contradict an explicit SLA.
The provider helper also rejects a conflicting reference override. Legacy core
rules still default to `_phlo_ingested_at` and the current clock.

`serialize_sla` emits references as ISO 8601 strings with their offset and omits
unset new fields. To reconstruct an SLA from JSON, parse its
`freshness_reference_time` with `datetime.fromisoformat` before calling `SLA`.
The same JSON-safe SLA appears in governance contracts and provider contract tags.
Existing quality events and durable run evidence retain evaluation metadata.

`@phlo.contract` and `@phlo.publish` declare governance metadata. They do not
execute quality checks. An author attaches the same SLA to the quality provider
to enforce it. Providers without neutral-rule translation reject
`@phlo.quality.rules` explicitly. Pandera is the workspace's freshness translator.

These checks do not prove completeness. Null or invalid values do not contribute
to the maximum, and one recent row can satisfy freshness despite missing events.
Use separate null, count, and reconciliation checks for those conditions.
Future timestamps are not rejected by an age upper bound. Validate them separately.
Dagster `RunSpec.freshness_hours` still measures materialisation age, not data age.

See the [runnable IoT example](../../examples/lakehouses/iot-telemetry/README.md#check-event-time-freshness-locally)
for a local Parquet, DuckDB, and durable-evidence demonstration.

## UniqueCheck

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `columns` | `list[str]` | required | Columns forming the uniqueness key. |
| `allow_threshold` | `float` | `0.0` | Maximum duplicate fraction. |

## CountCheck

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `min_rows` | `int \| None` | `None` | Minimum row count. |
| `max_rows` | `int \| None` | `None` | Maximum row count. |

## SchemaCheck

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `schema` | `Any` | required | Schema object used for validation. |
| `lazy` | `bool` | `true` | Collect all validation failures. |

## CustomSQLCheck

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name_` | `str` | required | Stable check name. |
| `sql` | `str` | required | SQL expression or query. |
| `expected` | `bool` | `true` | Expected SQL result. |
| `allow_threshold` | `float` | `0.0` | Permitted failure fraction. |

## PatternCheck

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `column` | `str` | required | String column. |
| `pattern` | `str` | required | Regular expression. |
| `allow_threshold` | `float` | `0.0` | Permitted nonmatching fraction. |
| `case_sensitive` | `bool` | `true` | Regular expression case mode. |

## ReconciliationCheck

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `source_table` | `str` | required | Comparison table. |
| `partition_column` | `str` | `_phlo_partition_date` | Partition field. |
| `check_type` | `str` | `rowcount_parity` | Reconciliation operation. |
| `tolerance` | `float` | `0.0` | Relative tolerance. |
| `absolute_tolerance` | `int \| float \| None` | `None` | Absolute tolerance. |
| `where_clause` | `str \| None` | `None` | Optional source filter. |

## AggregateConsistencyCheck

Compares configured aggregate values between source and target data. Its `aggregates` field contains aggregate specifications with an expression, output name, and tolerance.

## KeyParityCheck

Compares key sets between source and target relations. The configuration identifies source and target tables, key columns, and optional filtering.

## MultiAggregateConsistencyCheck

Evaluates multiple aggregate specifications in one check and reports each aggregate result independently.

## ChecksumReconciliationCheck

Compares checksums for selected columns or rows between source and target relations. Its configuration contains source and target relation details and checksum selection.

## Neutral quality rules

These factories return provider-neutral `QualityRule` values.

| Rule | Signature | Parameters |
| --- | --- | --- |
| `not_null` | `not_null(*columns)` | One or more columns. |
| `unique` | `unique(*columns)` | One or more columns. |
| `freshness` | `freshness(column, *, hours, reference_time=None, require_timestamps=False)` | Timestamp column, maximum age, optional evaluation reference, and missing-timestamp policy. |
| `range_between` | `range_between(column, *, min_value=None, max_value=None)` | At least one bound. |
| `accepted_values` | `accepted_values(column, values)` | Non-empty allowed values. |
| `grain` | `grain(*columns)` | Non-empty, distinct composite business-key columns. |
| `reconcile_aggregate` | `reconcile_aggregate(*, name, source, source_column, target_column, ...)` | Named numeric aggregate reconciliation. |

### Dataset grain

`phlo.grain("tenant_id", "order_id")` requires each composite identity to occur
once, with no SQL NULL component. Individual columns may repeat. An empty
relation passes; a missing key column fails. Floating-point NaN is not SQL NULL.
This is stronger than `unique`, which permits a single null identity.

### Aggregate reconciliation

`phlo.reconcile_aggregate` compares one numeric aggregate across a named source
relation and the quality decorator's target relation. Pandera executes both
aggregates in SQL through the same runtime-selected connection. Only scalar
results enter Python. Decimal comparisons preserve numeric precision, and
evidence stores scalar values as decimal strings.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `name` | required | Distinct durable evidence identity. |
| `source` | required | `schema.table`, `table`, or resolved `phlo.ref(...)`. |
| `source_column` | required | Numeric source column. |
| `target_column` | required | Numeric target column. |
| `aggregate` | `"sum"` | `sum`, `avg`, `min`, `max`, or `count`. `count` counts non-null column values, not rows. |
| `source_filters` | `None` | Source-only equality predicates as a column-to-value mapping. `None` values mean `IS NULL`. |
| `target_filters` | `None` | Independent target-only equality predicates. |
| `source_partition_column` | `None` | Source override for the decorator's partition column. |
| `absolute_tolerance` | `0.0` | Finite, non-negative, inclusive absolute difference. |

The decorator's exact partition key, rolling window, or explicit `full_table`
scope applies to both relations, before their independent filters. Source and
target columns must represent the same daily partition identity when partition
scoping is enabled. With no key, the existing default is a seven-day window.
`sum`, `avg`, `min`, and `max` ignore individual null values, following SQL
semantics. An empty or all-null scope has an undefined aggregate and fails,
even when both sides are empty. Zero equals zero. Empty `count` scopes produce
zero and can pass. Missing relations, missing columns, non-numeric aggregates,
and non-finite aggregate values fail rather than skip.

Constraint relations remain within the selected runtime catalog. A resolved
logical reference contributes its schema and table, not its physical catalog.
Trino uses the canonical runtime ref, including `phlo/wap_branch` precedence
over `phlo/ref`. DuckDB uses the supplied connection and does not implement
Nessie refs. Explicit catalog-qualified relation strings and custom `query`
overrides are rejected for these constraints. Define a named view for a custom
target query instead. Relation strings use simple SQL identifier segments;
column names and filter values use Phlo's identifier and literal helpers.

Declaration validates rule shape, supported aggregate functions, resolved
logical references, and evidence-name uniqueness. It does not validate physical
tables or columns against a live catalog at discovery, infer source asset
dependencies, or provide a cross-table snapshot lock. Authors must order source
materialisation before the quality check. SQL execution detects missing physical
references. Unresolved logical references fail at declaration rather than
silently falling back to an asset name. Other quality providers must explicitly
translate these rule kinds; unsupported translation raises an error.

These constraints emit the existing quality-result evidence. Orchestrator
`blocking` and `warn_threshold` settings do not replace Dataset publication
policy. `RunReportQualityEvidenceSource` binds the expected checks to an exact
project, run, and attempt. A blocker policy rejects failed evidence; a warning
policy reports the finding without refusing publication. Neither declaration
automatically promotes a physical Nessie ref. The
[lakehouse demonstration](../../examples/lakehouses/wap-failure-lab/README.md#dataset-constraint-demonstration)
contains complete declarations and a runnable before-and-after procedure.

## Severity and provider mapping

The Pandera provider reports check results as asset checks. Blocking checks fail the asset evaluation. Observe declarations create advisory checks with warning severity. DLT contract checks use the stable `PANDERA_CONTRACT_CHECK_NAME`. `dbt_check_name` maps a provider-neutral check to a dbt test name.
