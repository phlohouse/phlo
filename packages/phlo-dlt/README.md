# phlo-dlt

DLT (Data Load Tool) ingestion engine for Phlo.

## Description

Provides the `@phlo_ingestion` decorator for defining data ingestion pipelines using DLT. Automatically materializes data into Iceberg tables with schema evolution and lineage tracking.
Schema validation is supplied by installed quality-provider capabilities such as `phlo-pandera`;
`phlo-dlt` does not depend on a specific quality provider.

## Installation

```bash
pip install phlo-dlt
# or
phlo plugin install dlt
```

## Configuration

| Variable                 | Default               | Description                |
| ------------------------ | --------------------- | -------------------------- |
| `ICEBERG_WAREHOUSE_PATH` | `s3://lake/warehouse` | Iceberg warehouse S3 path  |
| `ICEBERG_STAGING_PATH`   | `s3://lake/stage`     | Staging path for ingestion |
| `NESSIE_HOST`            | `nessie`              | Nessie catalog host        |
| `NESSIE_PORT`            | `19120`               | Nessie catalog port        |

## Auto-Configuration

This package is **fully auto-configured**:

| Feature                | How It Works                                                         |
| ---------------------- | -------------------------------------------------------------------- |
| **Asset Registration** | Ingestion assets published as capability specs via asset provider entry points |
| **Lineage Events**     | Emits `ingestion.start`, `ingestion.end` events for lineage tracking |
| **Schema Evolution**   | Automatically handles schema changes during ingestion                |
| **Hook Integration**   | Events captured by alerting, metrics, and OpenMetadata plugins       |

### Event Flow

```
@phlo_ingestion → IngestionEventEmitter → HookBus → [Alerting, Metrics, Lineage plugins]
```

## Usage

### Defining Ingestion

```python
from phlo_dlt import phlo_ingestion
from dlt.sources.rest_api import rest_api

@phlo_ingestion(
    table_name="github_events",
    unique_key="id",
    group="bronze",
)
def ingest_github_events(partition_date: str):
    return rest_api(
        client={"base_url": "https://api.github.com"},
        resources=[{
            "name": "events",
            "endpoint": {"path": "/events"},
        }]
    )
```

### Running Ingestion

Ingestion assets are automatically discovered and can be materialized via the active orchestrator:

```bash
phlo materialize dlt_github_events
```

### Reviewed CSV identity and retention

New reviewed CSV uploads use `dlt_upload_<upload_id>` for their asset, checks,
events, and DLT staging pipeline identity. Uploads can use the same table name
on separate candidate references without colliding. The physical relation
remains `bronze.<table>`. Existing contracts without `asset_id` keep their
`dlt_<table>` identity. General ingestion declarations retain that default;
the optional `phlo_ingestion(asset_key=...)` separates asset and table identity.

Archive preserves CSV bytes and does not free quota. The authorised
`POST /api/v1/uploads/csv/{upload_id}/reclaim` action requires confirmation,
a reason, and `expected_checksum`. Reclamation deletes only the archived local
`source.csv`, then frees its retained-file and byte quota. The durable upload
record, immutable contract, generated workflow, receipts, checksums, candidate
evidence, and operation claims remain. Source detail returns HTTP 410 after
reclamation starts; inventory and source handoff remain available.
Reclaimed uploads cannot be restored or loaded again.

Pending, unknown, or incomplete mutations block reclamation. Interrupted local
cleanup can resume without rerunning ingestion or changing remote data.
The API also applies configured `audit.retention_years` to both the immutable
CSV payload age and its latest archival timestamp. Recent archival therefore
preserves supporting bytes even for an old upload. Unset retention adds no age
minimum. Audit records and tombstones have no automatic expiry.

## Entry Points

- `phlo.plugins.assets` - Provides `DltAssetProvider` for ingestion asset specs
