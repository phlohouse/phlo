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
| `freshness` | `freshness(column, *, hours)` | Timestamp column and maximum age. |
| `range_between` | `range_between(column, *, min_value=None, max_value=None)` | At least one bound. |
| `accepted_values` | `accepted_values(column, values)` | Non-empty allowed values. |

## Severity and provider mapping

The Pandera provider reports check results as asset checks. Blocking checks fail the asset evaluation. Observe declarations create advisory checks with warning severity. DLT contract checks use the stable `PANDERA_CONTRACT_CHECK_NAME`. `dbt_check_name` maps a provider-neutral check to a dbt test name.
