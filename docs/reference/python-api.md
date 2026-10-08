# Python API reference

The public APIs below are defined in `phlo`, `phlo-dlt`, `phlo-sling`, and `phlo-pandera`. Generated API pages are not emitted under the `python-reference` route by the current pymdx build, so this page does not link to that route.

## phlo.ingest.dlt

`phlo.ingest.dlt` resolves `phlo_dlt.phlo_ingestion`.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `table_name` | `str` | required | Destination table name. |
| `unique_key` | `str` | required | Merge and deduplication key. |
| `group` | `str` | required | Asset group. |
| `validation_schema` | `type[Any] \| None` | `None` | Pandera validation schema. |
| `table_schema` | `Any \| None` | `None` | Explicit table-store schema. |
| `partition_spec` | `Any \| None` | `None` | Provider partition specification. |
| `cron` | `str \| None` | `None` | Optional schedule expression. |
| `freshness_hours` | `tuple[int, int] \| None` | `None` | Warning and error freshness thresholds. |
| `max_runtime_seconds` | `int` | `300` | Maximum run duration. |
| `max_retries` | `int` | `3` | Maximum retry count. |
| `retry_delay_seconds` | `int` | `30` | Delay between retries. |
| `validate` | `bool` | `True` | Enable validation. |
| `strict_validation` | `bool` | `True` | Make validation failures blocking. |
| `merge_strategy` | `Literal["append", "merge"]` | `"merge"` | Append rows or merge on `unique_key`. |
| `merge_config` | `dict[str, Any] \| None` | `None` | Merge behaviour overrides. |
| `schema_policy` | `Literal["strict", "additive", "drop_extra"]` | `"strict"` | Schema handling for opted-in table stores. See [Iceberg write schema policies](#iceberg-write-schema-policies). |
| `add_metadata_columns` | `bool` | `True` | Add Phlo metadata columns. |
| `owner` | `str \| None` | `None` | Owning team. |
| `consumers` | `list[Consumer \| str] \| None` | `None` | Downstream consumers. |
| `sla` | `SLA \| None` | `None` | Service-level expectations. |
| `capabilities` | `dict[str, str] \| None` | `None` | Per-asset capability providers. |
| `partitioned` | `bool` | `True` | Require a partition key at runtime. |
| `quality_checks` | `Sequence[Callable[[pd.DataFrame], str \| None]] \| None` | `None` | Staged-data checks. |

```python
import phlo

@phlo.ingest.dlt(
    table_name="events",
    unique_key="event_id",
    group="csv",
    validation_schema=EventsSchema,
)
def load_events(partition_date: str) -> object:
    return read_events(partition_date)
```

### Iceberg write schema policies

`IcebergResource.ensure_table`, `append_parquet`, `merge_parquet`, and `overwrite_parquet` accept the keyword-only `schema_policy` argument. The storage helpers `ensure_table`, `append_to_table`, `merge_to_table`, and `overwrite_table` accept the same argument. All default to `"strict"`.

| Policy | Extra source columns | Existing columns |
| --- | --- | --- |
| `strict` | Rejected with an actionable schema error. | Validated and aligned to the target. |
| `additive` | New nullable columns are added using native Iceberg type conversion. Required additions are rejected. | No automatic type or nullability changes. |
| `drop_extra` | Discarded explicitly. | The same safety checks as strict mode. |

All three policies order columns by the target schema, fill missing optional columns with typed nulls, and reject missing or null required fields, including metadata fields. Signed integer and floating-point widening are permitted. Numeric narrowing, floating-point-to-integer casts, and string-to-number coercion are rejected, even when current values fit. Integer-to-floating-point casts require Arrow's exact-value safety check. Decimal casts must preserve scale and integer capacity. Timestamp casts preserve timezone semantics and reject lost precision. Nested columns must retain their structure, and required nested values are checked. Unsupported conversions fail before any data write.

Additive schema updates and data changes publish in the same transaction. Existing field IDs remain unchanged, and historical rows read null in added columns. A failed write does not publish staged schema updates. Competing compatible additions refresh and reconcile the complete write, with at most three attempts. Conflicting types or nullability definitions fail explicitly. Ordinary data conflicts without staged additions remain explicit failures.

`ensure_table` validates an existing declaration without evolving it. Actual additive evolution occurs only after the incoming batch passes validation. Declared changes to existing types or nullability require explicit migration. Missing optional source fields do not mean that the target fields are dropped.

Table stores opt in by advertising `TableStoreSupport.schema_policies` and satisfying the optional `phlo.capabilities.SchemaPolicyTableStore` contract. Iceberg advertises all three policies. The mandatory legacy `TableStore` contract is unchanged. Every supported policy-aware write must accept `schema_policy`; ingestion callers check the selected method signatures before any write, because runtime protocol checks validate attributes rather than signatures. DLT passes unprojected Parquet to opted-in stores so they can detect drift. Providers without this opt-in retain their existing default behaviour and reject explicit alternatives to that default.

Data migration specs expose `destination.schema_policy`, defaulting to `strict`. The executor forwards the selected policy for append, merge, and overwrite, including the append calls for later overwrite chunks. Column mapping preserves unmapped columns; use `drop_extra` for intentional projection rather than assuming mapping drops fields. Overwrite validates both replacement and subsequent append policy contracts before the first chunk is written.

Kafka consumer assets already expose `schema_policy`, defaulting to `additive`. Their direct table-store stager now forwards it to opted-in stores. Additive writes add new nullable fields only; an audit-compatible type widening does not authorise migration of an existing field. Invalid batches fail without committing their source offsets. Non-opted-in stores keep Kafka's existing default calls; explicit non-default policies are rejected. This forwarding applies to the table-store path, not the separate snapshot-promotion catalog API.

This replaces Iceberg's previous implicit extra-column dropping and warning-only cast failures. The documented migration path is [`schema_policy="drop_extra"`](../guides/ingest-data.md#choose-a-schema-policy) for intentional projection, plus corrected input types and explicit schema migrations where necessary. `drop_extra` does not permit lossy casts or missing required fields.

## phlo.ingest.sling

`phlo.ingest.sling` resolves `phlo_sling.phlo_sling_replication`.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `stream_name` | `str` | required | Source stream. |
| `table_name` | `str` | required | Destination table. |
| `source_conn` | `str` | required | Sling source connection name. |
| `group` | `str` | required | Asset group. |
| `target_conn` | `str \| None` | `None` | Sling target connection. |
| `mode` | `Literal["full-refresh", "incremental", "snapshot", "backfill"] \| None` | `None` | Replication mode. |
| `primary_key` | `list[str] \| str \| None` | `None` | Primary key fields. |
| `update_key` | `str \| None` | `None` | Incremental update field. |
| `object` | `str \| None` | `None` | Source object override. |
| `select` | `list[str] \| None` | `None` | Selected source columns. |
| `where` | `str \| None` | `None` | Source filter. |
| `source_options` | `dict[str, Any] \| None` | `None` | Source options. |
| `target_options` | `dict[str, Any] \| None` | `None` | Target options. |
| `cron` | `str \| None` | `None` | Optional schedule expression. |
| `freshness_hours` | `tuple[int, int] \| None` | `None` | Warning and error freshness thresholds. |
| `max_runtime_seconds` | `int` | `600` | Maximum run duration. |
| `max_retries` | `int` | `3` | Maximum retry count. |
| `retry_delay_seconds` | `int` | `30` | Delay between retries. |
| `owner` | `str \| None` | `None` | Owning team. |
| `consumers` | `list[Consumer \| str] \| None` | `None` | Downstream consumers. |
| `sla` | `SLA \| None` | `None` | Service-level expectations. |

```python
import phlo

@phlo.ingest.sling(
    stream_name="public.users",
    table_name="users",
    source_conn="PHLO_POSTGRES",
    group="ingestion",
    mode="incremental",
    update_key="updated_at",
)
def replicate_users(context: object) -> dict[str, str]:
    return {}
```

## phlo.quality.pandera

`phlo.quality.pandera` resolves `phlo_pandera.phlo_pandera`.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `table` | `str` | required | Table or asset relation. |
| `checks` | `List[QualityCheck]` | required | Quality checks to execute. |
| `asset_key` | `Optional[str]` | `None` | Override the asset key. |
| `group` | `Optional[str]` | `None` | Asset group. |
| `blocking` | `bool` | `True` | Fail the asset on a failed check. |
| `partition_aware` | `bool` | `True` | Scope checks to the current partition. |
| `warn_threshold` | `float` | `0.0` | Warning threshold. |
| `partition_column` | `str` | `"_phlo_partition_date"` | Partition column. |
| `rolling_window_days` | `int \| None` | `7` | Rolling evaluation window. |
| `full_table` | `bool` | `False` | Evaluate the full table. |
| `description` | `Optional[str]` | `None` | Asset description. |
| `query` | `Optional[str]` | `None` | Query override. |
| `backend` | `str` | `"trino"` | Query backend. |
| `owner` | `str \| None` | `None` | Owning team. |
| `consumers` | `list[Consumer \| str] \| None` | `None` | Downstream consumers. |
| `sla` | `SLA \| None` | `None` | Service-level expectations. |

```python
import phlo

@phlo.quality.pandera(table="raw.events", checks=[NullCheck(columns=["name"])])
def event_quality() -> object:
    return events
```

## phlo.quality.rules

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `table` | `str` | required | Table or asset relation. |
| `rules` | `list[Any]` | required | Provider-neutral quality rules. |
| `provider_name` | `str` | `"pandera"` | Quality provider. |
| `**kwargs` | `Any` | no default | Additional provider options. |

```python
import phlo

@phlo.quality.rules(
    table="raw.events",
    rules=[phlo.not_null("name"), phlo.unique("event_id")],
)
def event_rules() -> object:
    return events
```

## Neutral quality rules

### `not_null`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `columns` | `str` positional, variadic | required | Columns that must not contain null values. |

### `unique`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `columns` | `str` positional, variadic | required | Columns forming a unique key. |

### `freshness`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `column` | `str` | required | Timestamp column. |
| `hours` | `float` keyword-only | required | Maximum age in hours. |

### `range_between`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `column` | `str` | required | Numeric column. |
| `min_value` | `float \| int \| None` | `None` | Inclusive lower bound. |
| `max_value` | `float \| int \| None` | `None` | Inclusive upper bound. |

### `accepted_values`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `column` | `str` | required | Column to inspect. |
| `values` | `list[Any]` | required | Allowed values. |

```python
rules = [
    phlo.not_null("name"),
    phlo.unique("event_id"),
    phlo.freshness("updated_at", hours=24),
    phlo.range_between("value", min_value=0, max_value=100),
    phlo.accepted_values("status", ["open", "closed"]),
]
```

## phlo.flow decorators

Each decorator receives a function and registers a provider-neutral declaration.

### `publish`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `table` | `str` | required | Published table. |
| `audience` | `list[str] \| None` | `None` | Intended audience. |
| `owner` | `str \| None` | `None` | Owner. |
| `freshness_hours` | `int \| None` | `None` | Freshness expectation. |
| `depends_on` | `list[AssetDependency] \| None` | `None` | Asset dependencies. |
| `group` | `str` | `"publish"` | Asset group. |
| `consumers` | `list[Consumer \| str] \| None` | `None` | Consumers. |
| `sla` | `SLA \| None` | `None` | Service-level expectations. |
| `description` | `str \| None` | `None` | Asset description. |

```python
@phlo.publish(table="marts.daily_sales", owner="analytics")
def daily_sales() -> object:
    return build_daily_sales()
```

### `observe`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `table` | `str` | required | Observed table. |
| `freshness_hours` | `int \| None` | `None` | Warning freshness threshold. |
| `row_count_change` | `dict[str, float] \| None` | `None` | Row-count movement thresholds. |
| `depends_on` | `list[AssetDependency] \| None` | `None` | Asset dependencies. |
| `group` | `str` | `"observe"` | Asset group. |
| `description` | `str \| None` | `None` | Asset description. |

```python
@phlo.observe(table="marts.daily_sales", freshness_hours=24)
def daily_sales_observation() -> object:
    return daily_sales
```

### `backfill`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `target` | `str` | required | Target asset. |
| `partitions` | `dict[str, Any]` | required | Partition range or values. |
| `mode` | `str` | `"replace-partitions"` | Backfill mode. |
| `depends_on` | `list[AssetDependency] \| None` | `None` | Asset dependencies. |
| `group` | `str` | `"backfill"` | Asset group. |
| `owner` | `str \| None` | `None` | Owner. |
| `description` | `str \| None` | `None` | Asset description. |

```python
@phlo.backfill(target="daily_sales", partitions={"date": "2025-01-01"})
def rebuild_sales() -> object:
    return rebuild()
```

### `contract`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `table` | `str` | required | Contract table. |
| `owner` | `str \| None` | `None` | Owner. |
| `consumers` | `list[Consumer \| str] \| None` | `None` | Consumers. |
| `pii` | `bool` | `False` | Whether the table contains PII. |
| `freshness_hours` | `int \| None` | `None` | Freshness expectation used to build an SLA. |
| `lifecycle` | `str \| None` | `None` | Lifecycle label. |
| `sla` | `SLA \| None` | `None` | Service-level expectations. |
| `metadata` | `dict[str, Any] \| None` | `None` | Additional metadata. |

```python
@phlo.contract(table="marts.daily_sales", owner="analytics")
def daily_sales_contract() -> object:
    return daily_sales
```

### `access`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `table` | `str` | required | Protected table. |
| `roles` | `list[str]` | required | Roles with access. |
| `pii_columns` | `list[str] \| None` | `None` | PII columns. |
| `policy` | `str` | `"read"` | Access policy name. |
| `metadata` | `dict[str, Any] \| None` | `None` | Additional metadata. |

```python
@phlo.access(table="marts.daily_sales", roles=["analyst"])
def daily_sales_access() -> object:
    return daily_sales
```

### `schedule`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | `str` | required | Schedule name. |
| `cron` | `str` | required | Cron expression. |
| `targets` | `list[str]` | required | Static targets. |
| `timezone` | `str` | `"UTC"` | Schedule timezone. |
| `metadata` | `dict[str, Any] \| None` | `None` | Additional metadata. |

```python
@phlo.schedule(name="daily-sales", cron="0 2 * * *", targets=["daily_sales"])
def daily_sales_schedule() -> object:
    return {"run_date": "today"}
```

The `backfill` and `schedule` decorators emit deprecation warnings because no adapter executes their declarations. The `publish`, `observe`, `contract`, and `access` declarations feed the governance metadata plane.

## Contracts

### `Consumer`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | `str` | required | Consumer name. |
| `contact` | `str \| None` | `None` | Consumer contact. |
| `usage` | `str \| None` | `None` | Description of use. |

```python
consumer = phlo.Consumer(name="analytics", contact="analytics@example.com")
```

### `SLA`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `freshness_hours` | `int \| None` | `None` | Freshness limit. |
| `quality_threshold` | `float` | `1.0` | Required quality ratio. |
| `max_failures` | `int \| None` | `None` | Allowed failures. |
| `notify` | `list[str] \| None` | `None` | Notification recipients. |

```python
sla = phlo.SLA(freshness_hours=24, quality_threshold=0.99)
```

## Helpers and relation references

### `read_dataframe`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `query` | `str \| LogicalRelation` | required | SQL query or logical relation. |
| `params` | `list[object] \| tuple[object, ...] \| None` | `None` | Query parameters. |
| `query_engine` | `Any` | `None` | Query-engine override. |
| `runtime` | `Any` | `None` | Runtime context. |
| `schema` | `str \| None` | `None` | Schema override. |
| `schema_class` | `type[Any] \| None` | `None` | Result schema class. |

```python
frame = phlo.read_dataframe("select * from raw.events")
```

### `synthetic_key`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `dialect` | `str` | required | SQL dialect. |
| `fields` | `Iterable[str]` | required | Fields used in the key. |
| `namespace` | `str \| None` | `None` | Optional key namespace. |

```python
key = phlo.synthetic_key(dialect="trino", fields=["tenant_id", "event_id"])
```

### `quote_identifier`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `identifier` | `str` | required | Identifier to quote. |

```python
quoted = phlo.quote_identifier("event_id")
```

### `LogicalRelation`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `asset_key` | `str` | required | Logical asset key. |
| `catalog` | `str \| None` | `None` | Physical catalog. |
| `schema` | `str \| None` | `None` | Physical schema. |
| `table` | `str \| None` | `None` | Physical table. |
| `relation` | `str \| None` | `None` | Complete physical relation. |
| `metadata` | `Mapping[str, Any]` | `field(default_factory=dict)` | Relation metadata. |

```python
relation = phlo.LogicalRelation(asset_key="dlt_events", schema="raw", table="events")
```

### `ref`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | `str` | required | Asset name. |
| `registry` | `CapabilityRegistry \| None` | `None` | Registry override. |
| `discover` | `bool` | `True` | Discover capabilities before resolving. |

```python
events = phlo.ref("dlt_events")
```

### `source`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `source_name` | `str` | required | Source name. |
| `table_name` | `str` | required | Source table. |
| `registry` | `CapabilityRegistry \| None` | `None` | Registry override. |
| `discover` | `bool` | `True` | Discover capabilities before resolving. |

```python
users = phlo.source("crm", "users")
```
