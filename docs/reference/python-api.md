# Python API reference

The public APIs below are defined in `phlo`, `phlo-dlt`, `phlo-sling`, and `phlo-pandera`. Generated API pages are not emitted under the `python-reference` route by the current pymdx build, so this page does not link to that route.

## phlo.export

`phlo.export` registers one executable capability asset for a complete set of files. The writer accepts `phlo.exports.ExportContext` and returns `None`. The [runnable export guide](../guides/export-files.md) demonstrates discovery, execution, and consumption.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | `str` | required | Asset key and destination subdirectory. Must be a single non-empty path component. |
| `destination` | `str \| Path` | required | Local parent directory, resolved at declaration time. Remote URLs are unsupported. |
| `outputs` | `Mapping[str, str]` | required | Artifact names mapped to distinct relative file paths. At least one output is required. Absolute paths, parent traversal, and the reserved `manifest.json` path are rejected. |
| `depends_on` | `Sequence[str]` | `()` | Upstream asset keys registered as orchestration dependencies. |
| `group` | `str` | `"exports"` | Orchestration asset group. |
| `resources` | `Iterable[str]` | `()` | Required runtime resource keys. |
| `max_retries` | `int` | `0` | Retry count passed to `RunSpec`. |
| `retry_delay_seconds` | `int` | `30` | Retry delay passed to `RunSpec`. |

`ExportContext` contains `staging_dir`, the canonical `run_id`, the orchestrator-neutral `runtime`, and a mutable `upstream_versions: dict[str, str]`. Version keys must be declared dependencies. References describe the data selected by the writer. Missing references mean that the version is unavailable, not that an unversioned source has a fabricated version.

`resolve_export_manifest(destination, name)` reads `current.json` once, then returns the referenced `ArtifactManifest`. Each entry has an absolute local path in `uri`, SHA-256 checksum, byte size, and its artifact name in `metadata["name"]`. Manifest metadata contains `run_id`, `partition_key`, `ref`, and `upstream_versions`.

The materialisation metadata keys are `phlo/export_manifest`, `phlo/export_manifest_path`, `phlo/export_current_path`, and `phlo/export_run_id`. Static asset metadata includes `phlo/export_outputs` and `phlo/export_destination`.

### Local publication and retries

Each attempt gets a fresh temporary directory on the destination filesystem. Phlo copies only declared regular files into a separate complete-set directory, computes checksums, and writes its manifest. Symlink outputs and symlink parent directories are rejected. Undeclared scratch files are discarded.

Phlo renames the complete directory to `destination/name/runs/<sha256-of-run-id>` without replacing an existing complete run. It then atomically replaces `destination/name/current.json` with a pointer to that run's manifest. Consumers must reuse one resolved manifest for all files in a read operation.

Writer failures and missing outputs leave current unchanged. A failure to replace current can leave a complete but unreferenced run directory. Retrying the same identity with the same files and manifest metadata reuses that directory and retries the pointer replacement. Different content, different upstream references, or checksum corruption cause an error instead of overwriting the published run.

The canonical runtime routing run ID is required. Dagster step retries retain that ID. Dagster run re-execution uses the root run ID when available. The `phlo/run_id` run tag explicitly overrides the logical identity. A new export set requires a new logical identity, even if a writer would produce different files under a reused ID.

Successful concurrent runs use last-publication-wins at the atomic pointer replacement, not at start time. Prior run directories remain available without automatic deletion. Attempt directories are removed on normal success or exception, but a killed process can leave temporary directories.

The guarantee covers complete-set visibility on a local filesystem with atomic same-filesystem rename and replacement. It is not a power-loss durability guarantee, an object-storage commit protocol, or a guarantee for network filesystems. Files remain immutable through Phlo's API, not through filesystem permissions. External edits can corrupt them, and `verify_manifest_checksums` detects those edits. Workers must close writers before returning and must not mutate files in background tasks.

## phlo.ingest.dlt

`phlo.ingest.dlt` resolves `phlo_dlt.phlo_ingestion`.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `table_name` | `str` | required | Destination table name. |
| `unique_key` | `str` | required | Merge and deduplication key. History uses explicit identity in `merge_config`. |
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
| `merge_strategy` | `Literal["append", "merge", "history"]` | `"merge"` | Append rows, upsert on `unique_key`, or insert immutable versions. |
| `merge_config` | `dict[str, Any] \| None` | `None` | Merge overrides or required [history policy](#immutable-history-mode). |
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

### Immutable history mode

`merge_strategy="history"` inserts only unseen `(entity_key, version_key)` pairs. It skips committed identical versions and identical duplicates within the batch. Any payload conflict rejects the entire batch before a data or metadata commit. Distinct late versions remain stored regardless of arrival order. An identical replay keeps the first committed row, including its original arrival metadata.

| `merge_config` option | Requirement |
| --- | --- |
| `entity_key` | Explicit entity column name. |
| `version_key` | Explicit immutable version column name, distinct from `entity_key`. |
| `payload_columns` | Nonempty list of distinct comparison column names. Mutually exclusive with `payload_hash_column`. Order does not affect the policy. |
| `payload_hash_column` | Column containing a supplied stable string or binary payload hash. Mutually exclusive with `payload_columns`. The project owns hashing and collision risk. |

Every staged file must supply the selected columns. Identity and hash values cannot be null, empty, or non-finite. Identity values must be scalar. Payload values compare after safe schema alignment, with null equal to null and floating-point NaN equal to NaN, including nested values. Reserved `_phlo_` and `_dlt_` columns cannot define history identity or comparison. Other arrival metadata is excluded by selecting only payload fields. No identity generation or latest-version ordering is provided.

All staged files from one ingestion write form one batch. The provider reads them into memory, so batch size must fit available memory. Iceberg pushes exact incoming `(entity_key, version_key)` pairs into its scan predicates, in groups of at most 1,000 distinct pairs. It projects only identity and comparison fields; unrelated entities sharing version IDs are not materialised for comparison. Schema-policy validation applies before the history write. Compatible additions, policy binding, and inserted rows publish in one transaction. A replay with no new versions publishes no schema or policy changes.

`IcebergResource.history_parquet` accepts `table_name`, `data_paths`, a `HistoryPolicy` from `phlo.capabilities.history`, `override_ref`, `schema_policy`, and optional `evidence_context`. The storage helper is `phlo_iceberg.history.history_to_table`. Providers opt in with `TableStoreSupport.supports_history` and the optional `HistoryTableStore` protocol. Existing table-store providers need not implement a new mandatory method. Unsupported providers fail explicitly before table creation or writes.

Successful writes report `rows_inserted`, `rows_skipped`, `rows_conflicting`, and `rows_deleted=0` through operation evidence and materialisation metadata. Counts describe incoming rows, not attempts. For an unseen pair repeated three times, one row is inserted and two are skipped. For a committed identical pair repeated three times, all three are skipped. On conflict, `HistoryConflictError.metrics` reports zero inserted rows and counts all incoming rows belonging to conflicting pairs. Nonconflicting replay or duplicate rows contribute to `rows_skipped`; unseen rows rejected with the batch are not counted as inserted or skipped.

**Concurrency.** PyIceberg 0.11.1 append asserts the original snapshot head, including the absence of a snapshot on an empty table. Lookup is pinned to that head and uses the same loaded metadata as the transaction. A definite commit conflict reloads the table and repeats preparation, policy validation, lookup, comparison, and insertion. There are at most three whole-operation attempts. Append alone is never retried. Retry exhaustion fails explicitly.

An ambiguous commit exception, including a lost transport response, triggers fresh policy and version readback. `HistoryCommitUnknownError` then fails the operation, even if the versions are now present. Evidence records readback under `reconciliation`, with `outcome="unknown"`. Successful readback partitions distinct incoming identity pairs into `versions_present` (matching committed payload), `versions_missing` (absent), and `versions_conflicting` (different committed payload). These counts sum to the number of distinct incoming pairs, not incoming rows. Two duplicate observations of one committed version therefore report `versions_present=1`. Conflicting readback has `state="conflicting"`; unavailable readback has `state="unavailable"` without version counts. This evidence does not invent inserted or skipped counts from an uncertain commit. A subsequent explicit replay performs the complete comparison again.

**Policy metadata and migration.** Iceberg properties `phlo.history.policy` and `phlo.history.policy.sha256` contain the canonical policy and its SHA-256 fingerprint. The definition includes entity and version columns, comparison mode, payload fields, Iceberg field IDs, types, and policy format version. The first nonempty insertion binds these properties atomically. An unmarked nonempty table is rejected rather than adopted. Missing or incompatible policy metadata requires migration.

Policy changes require quiesced writers and validated data/schema migration before rebinding the properties. A snapshot guard does not detect property-only changes. Renames, replaced field IDs, changed comparison types, and altered identity or payload selection cannot silently redefine existing history. Migration must validate uniqueness and payload consistency under the new policy. This API provides no live policy-edit or migration bypass.

The uniqueness guarantee applies to cooperating history writers on the same table/ref. Ordinary append, merge, overwrite, rollback, external writers, and independent refs do not enforce history semantics. They must not mutate a table concurrently with history ingestion or migration.

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

This dormant decorator raises `NotImplementedError` and is removed in 0.19.0.
The `phlo backfill` CLI remains supported for partitioned provider assets.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `target` | `str` | required | Target asset. |
| `partitions` | `dict[str, Any]` | required | Partition range or values. |
| `mode` | `str` | `"replace-partitions"` | Backfill mode. |
| `depends_on` | `list[AssetDependency] \| None` | `None` | Asset dependencies. |
| `group` | `str` | `"backfill"` | Asset group. |
| `owner` | `str \| None` | `None` | Owner. |
| `description` | `str \| None` | `None` | Asset description. |

```bash
phlo backfill daily_sales --start-date 2025-01-01 --end-date 2025-01-07
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

This dormant decorator raises `NotImplementedError` and is removed in 0.19.0.
Provider decorators with `cron` support and native Dagster schedules remain supported.

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | `str` | required | Schedule name. |
| `cron` | `str` | required | Cron expression. |
| `targets` | `list[str]` | required | Static targets. |
| `timezone` | `str` | `"UTC"` | Schedule timezone. |
| `metadata` | `dict[str, Any] \| None` | `None` | Additional metadata. |

```python
import phlo
import pandera.pandas as pa

class SalesSchema(pa.DataFrameModel):
    date: str
    sales: int

@phlo.ingest.dlt(
    table_name="daily_sales",
    unique_key="date",
    group="sales",
    validation_schema=SalesSchema,
    cron="0 2 * * *",
)
def daily_sales_source():
    yield {"date": "2025-01-01", "sales": 42}
```

The `publish`, `observe`, `contract`, and `access` declarations feed the governance metadata plane.
No shipped Phlo package currently supplies the `transform` transformation and
asset providers required by `phlo.transform.sql`. Without both providers, it raises
`ModuleNotFoundError` before capturing SQL or registering an asset. Use dbt or
explicit asset-provider declarations instead. A separately installed plugin that
supplies both providers can enable SQL authoring through this decorator.

## Removal schedule

Deprecated top-level ingestion aliases and `phlo_quality` are removed in 0.19.0.
Warnings name that release and the replacement API. The callable-module shim
for `phlo.ingestion(...)` remains only until 0.19.0. The
`phlo migrate decorators-2026-05 PATH` codemod remains through at least 0.20.0.
Its C901 exception ends only when the codemod is removed.

The deprecated sync/async operation adapters, legacy `TransformationPlugin`,
Dagster `IngestionEnginePlugin`, and regulated-mode aliases also have a 0.19.0
removal deadline. `TransformationProviderPlugin` and `AssetProviderPlugin`
remain supported provider boundaries.

Compatibility-path usage counters are canonical observe metrics, with one
sample of value `1` per use. Summing the `sum` field of `metric.summary` events
over the observation window gives the usage count. Telemetry must be enabled
with a configured drain; disabled or missing telemetry does not prove zero use.

| Compatibility path | Removal release | Counter | What counts | Replacement |
| --- | --- | --- | --- | --- |
| `PHLO_REGULATED_MODE` | 0.19.0 | `phlo.legacy.regulated_mode_env.uses` | Each non-empty fallback read, not values overridden by canonical environment or explicit config | `PHLO_REGULATED` |
| Legacy Dagster env files | 0.19.0 | `phlo.legacy.dagster_env_file.uses` | Each existing `.env` or `.env.local` attachment per generated Phlo-dev service, tagged by relative filename | `phlo services migrate` to `overrides/.env` and `secrets/.env` |
| Legacy MCP JSONL tracing | 0.19.0 | `phlo.legacy.mcp_jsonl_span.uses` | Each successful debug span write, not configuration or canonical-only operations | `OBSERVE_DRAINS` or `OBSERVE_HTTP_ENDPOINT` |

A release owner can remove these paths earlier after confirming zero use across
enabled deployment telemetry. Otherwise the 0.19.0 deadline applies. MCP's
legacy JSONL readers retire with its writer; canonical trace queries remain supported.

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
