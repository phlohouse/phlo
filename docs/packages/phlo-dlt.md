# phlo-dlt

DLT (Data Load Tool) ingestion engine for Phlo.

## Overview

`phlo-dlt` provides the `phlo.ingest.dlt(...)` decorator for defining data ingestion pipelines using DLT. It materializes data into the active `table_store` with schema evolution and full lineage tracking.
Schema validation is supplied by installed quality-provider capabilities such as `phlo-pandera`;
`phlo-dlt` does not depend on a specific quality provider.

When multiple `table_store` providers are installed, selection is deterministic:

- workflow/runtime tag via `phlo/capability/table_store=...`
- asset override via `capabilities={"table_store": "..."}`
- global default via `phlo.yaml` `capabilities.defaults` or `PHLO_DEFAULT_CAPABILITIES`

## Installation

```bash
pip install phlo-dlt
# or
phlo plugin install dlt
```

## Configuration

| Variable                    | Default | Description                              |
| --------------------------- | ------- | ---------------------------------------- |
| `PHLO_DLT_DEFAULT_NAMESPACE`| `raw`   | Default namespace/schema for table names |

Note: Additional configuration for table storage comes from the active table_store provider (e.g., `phlo-iceberg` or `phlo-delta`).

## Features

### Auto-Configuration

| Feature                | How It Works                                                         |
| ---------------------- | -------------------------------------------------------------------- |
| **Asset Registration** | Ingestion assets published as capability specs via asset provider entry points |
| **Lineage Events**     | Emits `ingestion.start`, `ingestion.end` events for lineage tracking |
| **Schema Evolution**   | Automatically handles schema changes during ingestion                |
| **Hook Integration**   | Events captured by alerting, metrics, and OpenMetadata plugins       |

### Event Flow

```mermaid
flowchart LR
    ingestion["phlo.ingest.dlt(...)"]
    emitter[IngestionEventEmitter]
    hookbus[HookBus]
    plugins["Alerting, Metrics, Lineage plugins"]

    ingestion --> emitter --> hookbus --> plugins
```

## Usage

### Basic Ingestion

```python
import phlo
from phlo.contracts import Consumer, SLA
from workflows.schemas.events import EventSchema

@phlo.ingest.dlt(
    table_name="events",
    unique_key="id",
    validation_schema=EventSchema,
    group="api",
    cron="0 */1 * * *",
    freshness_hours=(1, 24),
    owner="platform-ingestion",
    consumers=[
        Consumer(name="analytics", usage="daily_reporting"),
        "fraud_team",
    ],
    sla=SLA(freshness_hours=2, quality_threshold=0.99),
)
def api_events(partition_date: str):
    """Ingest events from REST API."""
    from dlt.sources.rest_api import rest_api

    return rest_api(
        client={"base_url": "https://api.example.com"},
        resources=[{"name": "events", "endpoint": {"path": "events"}}],
    )
```

The `phlo.ingestion(...)` compatibility alias still exists for older workflows, but
new code should use the provider-neutral `phlo.ingest.dlt(...)` API.

```python
import phlo

@phlo.ingest.dlt(
    table_name="legacy_events",
    unique_key="id",
    group="api",
)
def legacy_events():
    ...
```

### Partitioned SQL Sources

For warehouse or operational database ingestion, `phlo-dlt` includes a focused
helper that loads a packaged `.sql` template, binds one partition window, fetches
rows in batches, normalizes column names, and adds static row defaults.

```python
import phlo
from phlo_dlt import PartitionWindow, PartitionedSqlConfig, partitioned_sql_source
from workflows.schemas.events import EventSchema
from workflows.sources.postgres import connect_source

sql_config = PartitionedSqlConfig(
    sql_template_package="workflows.sql",
    sql_template_name="events.sql",
    fetch_size=5000,
    row_defaults={"source_system": "app_postgres"},
)

@phlo.ingest.dlt(
    table_name="events",
    unique_key="event_id",
    validation_schema=EventSchema,
    group="app",
)
def app_events(partition_date: str):
    return partitioned_sql_source(
        sql_config,
        window=PartitionWindow(
            partition_key=partition_date,
            start=partition_date,
            end=partition_date,
        ),
        connect=connect_source,
        source_name="app_events",
        resource_name="events",
        primary_key="event_id",
        write_disposition="merge",
    )
```

The SQL template receives `partition_key`, `partition_start`, and `partition_end`
as DB-API parameters, plus any static `params` configured on `PartitionedSqlConfig`.

### Decorator Options

| Option              | Type              | Description                                         |
| ------------------- | ----------------- | --------------------------------------------------- |
| `table_name`        | `str`             | Target table-store table name                       |
| `unique_key`        | `str`             | Column for deduplication                            |
| `validation_schema` | `type`            | Provider-owned schema for validation                |
| `table_schema`      | `Any`             | Explicit table-store schema (optional)              |
| `group`             | `str`             | Asset group name                                    |
| `cron`              | `str`             | Schedule expression                                 |
| `freshness_hours`   | `tuple[int, int]` | (warn, fail) freshness thresholds                   |
| `merge_strategy`    | `str`             | `merge` (default) or `append`                       |
| `merge_config`      | `dict`            | Advanced merge configuration                        |
| `owner`             | `str`             | Optional owner/team metadata for contracts          |
| `consumers`         | `list[Consumer \| str]` | Optional downstream consumer metadata         |
| `sla`               | `SLA`             | Optional freshness/quality contract metadata        |
| `capabilities`      | `dict[str, str]`  | Optional capability provider overrides for the asset |
| `validate`          | `bool`            | Run configured provider validation using `validation_schema` (default: True) |
| `strict_validation` | `bool`            | Fail on validation errors (default: True)           |
| `max_runtime_seconds`| `int`            | Maximum runtime before timeout (default: 300)       |
| `max_retries`       | `int`             | Maximum retry attempts (default: 3)                 |
| `retry_delay_seconds`| `int`            | Delay between retries (default: 30)                 |
| `add_metadata_columns`| `bool`          | Inject Phlo metadata columns (default: True)        |

### Merge Strategies

```python
import phlo

# Default merge with deduplication
@phlo.ingest.dlt(
    table_name="events",
    unique_key="id",
    group="api",
    merge_strategy="merge",
    merge_config={"deduplication_method": "last"}  # or "first"
)
def merge_events():
    ...

# Append-only (no deduplication)
@phlo.ingest.dlt(
    table_name="events",
    unique_key="id",
    group="api",
    merge_strategy="append"
)
def append_events():
    ...
```

When `table_schema` is omitted, the active `table_store` provider must implement
schema derivation from `validation_schema` (for example Iceberg provider conversion).

### Selecting a Table Store

```python
import phlo
from workflows.schemas.events import EventSchema

@phlo.ingest.dlt(
    table_name="events",
    unique_key="id",
    validation_schema=EventSchema,
    group="api",
    capabilities={"table_store": "delta"},
)
def api_events(partition_date: str):
    ...
```

For workflow-wide selection, set the Dagster run tag
`phlo/capability/table_store=<provider>`.

### Running Ingestion

Ingestion assets are materialized via the orchestrator (e.g., Dagster). The asset key is
`dlt_{table_name}` (for example, `dlt_events` for table_name="events").

Use the orchestrator's UI or API to trigger materialization:

```bash
# Example with Dagster (when configured)
dagster asset materialize --select dlt_events
```

## Data Flow

```
External API
     ↓
DLT Pipeline (extract + normalize)
     ↓
Parquet Staging (S3)
     ↓
Pandera Validation
     ↓
Table Store Merge
     ↓
Physical Table (for active store)
```

## Entry Points

| Entry Point                   | Plugin                                  |
| ----------------------------- | --------------------------------------- |
| `phlo.plugins.assets`         | `DltAssetProvider` for ingestion specs  |
| `phlo.plugins.ingestion_providers` | `DLTIngestionProvider` for decorator |
| `phlo.plugins.cli`            | `DltCliPlugin` for workflow scaffolding |

## Related Packages

- [phlo-dagster](phlo-dagster.md) - Dagster adapter for capability specs
- [phlo-iceberg](phlo-iceberg.md) - Iceberg table format
- [phlo-pandera](phlo-pandera.md) - Data validation
- [phlo-nessie](phlo-nessie.md) - Branch management

## Next Steps

- [Developer Guide](../guides/developer-guide.md) - Master decorators
- [Workflow Development](../guides/workflow-development.md) - Build pipelines
- [Core Concepts](../getting-started/core-concepts.md) - Understand patterns
