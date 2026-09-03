# phlo-api Reference

Complete API reference for the Phlo Observatory backend service.

## Overview

The phlo-api is a FastAPI-based backend service that provides Observatory UI with access to:

- **Plugin & Service Management**: Discover and manage phlo plugins and services
- **Data Querying**: Execute queries against Trino and Iceberg tables
- **Orchestration**: Interact with Dagster assets and runs
- **Data Catalog**: Manage Nessie branches and catalog metadata
- **Quality Monitoring**: Query data quality check results
- **Logging**: Search and correlate logs via Loki
- **Lineage Tracking**: Row-level lineage queries
- **Maintenance**: Iceberg maintenance operation status
- **Search**: Unified search index across assets, tables, and columns

## Base URL

```
http://localhost:4000
```

## Core Endpoints

### Health & Configuration

#### `GET /health`

Health check endpoint.

**Response:**
```json
{
  "status": "healthy"
}
```

#### `GET /api/config`

Get phlo.yaml configuration.

**Response:**
```json
{
  "name": "my-project",
  "description": "My data lakehouse",
  "infrastructure": { ... }
}
```

### Plugin Management

#### `GET /api/plugins`

List all installed plugins grouped by type.

**Response:**
```json
{
  "source_connectors": ["rest_api", "csv_loader"],
  "quality_checks": ["null_check", "threshold_check"],
  "transformations": [],
  "services": ["dagster", "postgres", "trino", "nessie", "minio"]
}
```

#### `GET /api/plugins/{plugin_type}`

List plugins of a specific type.

**Path Parameters:**
- `plugin_type`: Type of plugins (source_connectors, quality_checks, transformations, services)

**Response:**
```json
["rest_api", "csv_loader"]
```

#### `GET /api/plugins/{plugin_type}/{name}`

Get detailed plugin information.

**Path Parameters:**
- `plugin_type`: Plugin type
- `name`: Plugin name

**Response:**
```json
{
  "name": "dagster",
  "version": "0.1.0",
  "description": "Dagster orchestration engine",
  "author": "Phlo Team",
  "license": "MIT"
}
```

### Service Management

#### `GET /api/services`

List all discovered services.

**Response:**
```json
[
  {
    "name": "dagster",
    "description": "Dagster orchestration engine",
    "category": "orchestration",
    "default": true,
    "profile": null,
    "core": true
  }
]
```

#### `GET /api/services/{name}`

Get detailed service information.

**Path Parameters:**
- `name`: Service name

**Response:**
```json
{
  "name": "dagster",
  "description": "Dagster orchestration engine",
  "category": "orchestration",
  "default": true,
  "profile": null,
  "depends_on": ["postgres"],
  "env_vars": {...},
  "core": true
}
```

### Plugin Registry

#### `GET /api/registry`

Get the plugin registry (available plugins for installation).

**Response:**
```json
{
  "plugins": {
    "phlo-superset": {
      "version": "0.1.0",
      "description": "Apache Superset BI tool"
    }
  }
}
```

### API Backend Discovery

#### `GET /api/backends`

List capability-backed API and graph-serving backends.

**Response:**
```json
[
  {
    "name": "hasura",
    "healthy": true,
    "metadata": {
      "backend_kind": "graphql",
      "service_name": "hasura",
      "category": "api"
    },
    "description": {
      "service_name": "hasura",
      "backend_kind": "graphql",
      "default_path": "/v1/graphql",
      "health_path": "/healthz",
      "metadata_path": "/v1/metadata",
      "base_url": "http://localhost:8081"
    }
  }
]
```

#### `GET /api/backends/{name}`

Get detailed information for one capability-backed API backend.

## Trino Query Engine

Base path: `/api/trino`

### Connection

#### `GET /api/trino/connection`

Check if Trino is reachable.

**Query Parameters:**
- `trino_url` (optional): Override Trino URL

**Response:**
```json
{
  "connected": true,
  "cluster_version": "461"
}
```

### Data Preview

#### `GET /api/trino/preview/{table}`

Preview data from a table with pagination.

**Path Parameters:**
- `table`: Table name or fully qualified name (catalog.schema.table)

**Query Parameters:**
- `branch` (default: "main"): Nessie branch
- `catalog` (optional): Catalog name (default: "iceberg")
- `schema` (optional): Schema name (default: branch)
- `limit` (default: 100, max: 5000): Number of rows
- `offset` (default: 0): Row offset
- `trino_url` (optional): Override Trino URL
- `timeout_ms` (default: 30000, max: 120000): Query timeout

**Response:**
```json
{
  "columns": ["id", "timestamp", "value"],
  "column_types": ["varchar", "timestamp", "double"],
  "rows": [
    {"id": "abc123", "timestamp": "2025-01-01T00:00:00", "value": 42.5}
  ],
  "has_more": false
}
```

### Column Profiling

#### `GET /api/trino/profile/{table}/{column}`

Get column statistics and profile.

**Path Parameters:**
- `table`: Table name
- `column`: Column name

**Query Parameters:**
- `branch`, `catalog`, `schema`, `trino_url`, `timeout_ms`: Same as preview

**Response:**
```json
{
  "column": "value",
  "type": "unknown",
  "null_count": 5,
  "null_percentage": 2.5,
  "distinct_count": 180,
  "min_value": "10.5",
  "max_value": "98.3"
}
```

### Table Metrics

#### `GET /api/trino/metrics/{table}`

Get table-level metrics.

**Path Parameters:**
- `table`: Table name

**Query Parameters:**
- `branch`, `catalog`, `schema`, `trino_url`, `timeout_ms`: Same as preview

**Response:**
```json
{
  "row_count": 10523
}
```

### Query Execution

#### `POST /api/trino/query`

Execute an arbitrary SQL query with read-only guardrails.

**Request Body:**
```json
{
  "query": "SELECT * FROM iceberg.main.my_table LIMIT 10",
  "branch": "main",
  "catalog": "iceberg",
  "schema": "main",
  "trino_url": null,
  "timeout_ms": 30000,
  "read_only_mode": true,
  "default_limit": 100,
  "max_limit": 5000
}
```

**Response:**
```json
{
  "columns": ["col1", "col2"],
  "column_types": ["varchar", "int"],
  "rows": [...],
  "has_more": false,
  "effective_query": "SELECT * FROM iceberg.main.my_table LIMIT 10"
}
```

**Error Response:**
```json
{
  "ok": false,
  "error": "INSERT statements are not allowed in read-only mode",
  "kind": "validation"
}
```

### Query with Filters

#### `POST /api/trino/query-with-filters`

Query a table with simple equality filters.

**Request Body:**
```json
{
  "table_name": "my_table",
  "schema": "main",
  "catalog": "iceberg",
  "filters": {
    "status": "active",
    "type": "user"
  },
  "limit": 10,
  "trino_url": null,
  "timeout_ms": 30000
}
```

**Response:**
Same as data preview.

### Row Retrieval

#### `GET /api/trino/row/{table}/{row_id}`

Get a single row by its `_phlo_row_id`.

**Path Parameters:**
- `table`: Table name
- `row_id`: Row ID value

**Query Parameters:**
- `catalog`, `schema`, `trino_url`, `timeout_ms`: Same as preview

**Response:**
Same as data preview (single row).

## Iceberg Tables

Base path: `/api/iceberg`

### List Tables

#### `GET /api/iceberg/tables`

Get all tables from Iceberg catalog.

**Query Parameters:**
- `branch` (default: "main"): Nessie branch
- `catalog` (optional): Catalog name
- `preferred_schema` (optional): Prioritize specific schema
- `trino_url` (optional): Override Trino URL
- `timeout_ms` (default: 30000, max: 120000): Query timeout

**Response:**
```json
[
  {
    "catalog": "iceberg",
    "schema_name": "bronze",
    "name": "dlt_entries",
    "full_name": "\"iceberg\".\"bronze\".\"dlt_entries\"",
    "layer": "bronze"
  },
  {
    "catalog": "iceberg",
    "schema_name": "silver",
    "name": "stg_entries",
    "full_name": "\"iceberg\".\"silver\".\"stg_entries\"",
    "layer": "silver"
  }
]
```

**Layers:**
- `bronze`: Raw ingestion (tables starting with `dlt_`)
- `silver`: Staged/cleaned (tables starting with `stg_`)
- `gold`: Curated facts/dimensions (tables starting with `fct_`, `dim_`)
- `publish`: Marts for BI (tables starting with `mrt_`, `publish_`)
- `unknown`: Cannot infer layer

### Table Schema

#### `GET /api/iceberg/tables/{table}/schema`

Get column schema for a table.

**Path Parameters:**
- `table`: Table name

**Query Parameters:**
- `schema` (optional): Schema name
- `branch`, `catalog`, `trino_url`, `timeout_ms`: Same as tables

**Response:**
```json
[
  {
    "name": "id",
    "type": "varchar",
    "nullable": false,
    "comment": "Unique identifier"
  },
  {
    "name": "timestamp",
    "type": "timestamp(6) with time zone",
    "nullable": true,
    "comment": null
  }
]
```

### Row Count

#### `GET /api/iceberg/tables/{table}/row-count`

Get row count for a table.

**Path Parameters:**
- `table`: Table name

**Query Parameters:**
- `branch`, `catalog`, `trino_url`, `timeout_ms`: Same as tables

**Response:**
```json
10523
```

### Table Metadata

#### `GET /api/iceberg/tables/{table}/metadata`

Get full table metadata including schema and row count.

**Path Parameters:**
- `table`: Table name

**Query Parameters:**
- `branch`, `catalog`, `trino_url`, `timeout_ms`: Same as tables

**Response:**
```json
{
  "table": {
    "catalog": "iceberg",
    "schema_name": "main",
    "name": "my_table",
    "full_name": "\"iceberg\".\"main\".\"my_table\"",
    "layer": "gold"
  },
  "columns": [...],
  "row_count": 10523,
  "last_modified": null
}
```

## Dagster Assets

Base path: `/api/dagster`

### Connection

#### `GET /api/dagster/connection`

Check if Dagster GraphQL is reachable.

**Query Parameters:**
- `dagster_url` (optional): Override Dagster GraphQL URL

**Response:**
```json
{
  "connected": true,
  "version": "1.9.7"
}
```

### Health Metrics

#### `GET /api/dagster/health`

Get health metrics from Dagster.

**Query Parameters:**
- `dagster_url` (optional): Override Dagster GraphQL URL

**Response:**
```json
{
  "assets_total": 25,
  "assets_healthy": 23,
  "failed_jobs_24h": 2,
  "quality_checks_passing": 45,
  "quality_checks_total": 50,
  "stale_assets": 2,
  "last_updated": "2025-01-15T10:30:00"
}
```

### List Assets

#### `GET /api/dagster/assets`

Get all assets from Dagster.

**Query Parameters:**
- `dagster_url` (optional): Override Dagster GraphQL URL

**Response:**
```json
[
  {
    "id": "asset-id-123",
    "key": ["dlt_glucose_entries"],
    "key_path": "dlt_glucose_entries",
    "description": "Glucose entries from Nightscout API",
    "compute_kind": "dlt",
    "group_name": "nightscout",
    "has_materialize_permission": true,
    "last_materialization": {
      "timestamp": "1705315200000",
      "run_id": "abc-123"
    }
  }
]
```

### Asset Details

#### `GET /api/dagster/assets/{asset_key_path}`

Get detailed information about a single asset.

**Path Parameters:**
- `asset_key_path`: Asset key path (e.g., "dlt_glucose_entries" or "namespace/asset_name")

**Query Parameters:**
- `dagster_url` (optional): Override Dagster GraphQL URL

**Response:**
```json
{
  "id": "asset-id-123",
  "key": ["dlt_glucose_entries"],
  "key_path": "dlt_glucose_entries",
  "description": "Glucose entries from Nightscout API",
  "compute_kind": "dlt",
  "group_name": "nightscout",
  "has_materialize_permission": true,
  "op_names": ["dlt_glucose_entries_op"],
  "metadata": [
    {"key": "table_name", "value": "entries"}
  ],
  "columns": [
    {"name": "id", "type": "VARCHAR", "description": "Entry ID"},
    {"name": "sgv", "type": "INTEGER", "description": "Blood glucose value"}
  ],
  "column_lineage": {
    "sgv": [
      {"asset_key": ["raw_api_response"], "column_name": "glucose_mg_dl"}
    ]
  },
  "partition_definition": {
    "description": "Daily partitioned by date"
  },
  "last_materialization": {
    "timestamp": "1705315200000",
    "run_id": "abc-123"
  }
}
```

### Materialization History

#### `GET /api/dagster/assets/{asset_key_path}/history`

Get materialization history for an asset.

**Path Parameters:**
- `asset_key_path`: Asset key path

**Query Parameters:**
- `limit` (default: 20, max: 100): Number of materializations
- `dagster_url` (optional): Override Dagster GraphQL URL

**Response:**
```json
[
  {
    "timestamp": "1705315200000",
    "run_id": "abc-123",
    "status": "SUCCESS",
    "step_key": "dlt_glucose_entries",
    "metadata": [
      {"key": "rows_inserted", "value": "288"},
      {"key": "duration_ms", "value": "1234"}
    ],
    "duration": null
  }
]
```

### Asset Materialization Dry Run

#### `POST /api/dagster/assets/{asset_key_path}/materialize`

Validate an asset materialization request. Live launch is not implemented yet;
send `dry_run: true` to validate the request shape and Dagster materialization
permission.

**Path Parameters:**
- `asset_key_path`: Asset key path

**Request:**
```json
{
  "dry_run": true,
  "partition_key": "2026-04-26"
}
```

**Response:**
```json
{
  "operation": "materialize_asset",
  "dry_run": true,
  "accepted": true,
  "run_id": null,
  "asset_key_path": "silver/orders",
  "partition_key": "2026-04-26",
  "status": "DRY_RUN",
  "message": "Materialization request is valid.",
  "details": {"op_names": ["orders_op"]}
}
```

### Run Status

#### `GET /api/dagster/runs/{run_id}/status`

Get the current Dagster status for a run.

**Response:**
```json
{
  "run_id": "abc-123",
  "status": "FAILURE",
  "pipeline_name": "daily_orders",
  "start_time": 1760000000.0,
  "end_time": 1760000100.0,
  "tags": {"asset": "silver/orders"}
}
```

### Run Retry Dry Run

#### `POST /api/dagster/runs/{run_id}/retry`

Validate a Dagster run retry request. Live retry launch is not implemented yet;
send `dry_run: true` to validate whether the run is a retry candidate.

**Request:**
```json
{
  "dry_run": true
}
```

**Response:**
```json
{
  "operation": "retry_failed_run",
  "dry_run": true,
  "accepted": true,
  "run_id": "abc-123",
  "asset_key_path": null,
  "partition_key": null,
  "status": "DRY_RUN",
  "message": "Run retry request is valid.",
  "details": {"run_status": "FAILURE"}
}
```

## Nessie Catalog

Base path: `/api/nessie`

### Connection

#### `GET /api/nessie/connection`

Check if Nessie is reachable.

**Query Parameters:**
- `nessie_url` (optional): Override Nessie URL

**Response:**
```json
{
  "connected": true,
  "default_branch": "main"
}
```

### List Branches

#### `GET /api/nessie/branches`

Get all branches and tags.

**Query Parameters:**
- `nessie_url` (optional): Override Nessie URL

**Response:**
```json
[
  {
    "type": "BRANCH",
    "name": "main",
    "hash": "abc123def456"
  },
  {
    "type": "BRANCH",
    "name": "dev",
    "hash": "def456ghi789"
  }
]
```

### Get Branch

#### `GET /api/nessie/branches/{branch_name}`

Get branch details by name.

**Path Parameters:**
- `branch_name`: Branch name

**Query Parameters:**
- `nessie_url` (optional): Override Nessie URL

**Response:**
```json
{
  "type": "BRANCH",
  "name": "main",
  "hash": "abc123def456"
}
```

### Commit History

#### `GET /api/nessie/branches/{branch_name}/history`

Get commit history for a branch.

**Path Parameters:**
- `branch_name`: Branch name

**Query Parameters:**
- `limit` (default: 50, max: 200): Number of commits
- `nessie_url` (optional): Override Nessie URL

**Response:**
```json
[
  {
    "commit_meta": {
      "hash": "abc123",
      "message": "Add glucose readings table",
      "committer": "phlo-dagster",
      "authors": ["user@example.com"],
      "commit_time": "2025-01-15T10:00:00Z",
      "author_time": "2025-01-15T10:00:00Z",
      "parent_commit_hashes": ["def456"]
    },
    "parent_commit_hash": "def456",
    "operations": [...]
  }
]
```

### Branch Contents

#### `GET /api/nessie/branches/{branch_name}/entries`

Get contents (tables) at a specific branch/ref.

**Path Parameters:**
- `branch_name`: Branch name

**Query Parameters:**
- `prefix` (optional): Filter by namespace prefix
- `nessie_url` (optional): Override Nessie URL

**Response:**
```json
[
  {
    "name": {"elements": ["entries"]},
    "type": "ICEBERG_TABLE",
    "contentId": "..."
  }
]
```

### Compare Branches

#### `GET /api/nessie/diff/{from_branch}/{to_branch}`

Compare two branches (diff).

**Path Parameters:**
- `from_branch`: Source branch
- `to_branch`: Target branch

**Query Parameters:**
- `nessie_url` (optional): Override Nessie URL

**Response:**
```json
{
  "diffs": [
    {
      "key": {"elements": ["my_table"]},
      "from": {...},
      "to": {...}
    }
  ]
}
```

### Create Branch

#### `POST /api/nessie/branches`

Create a new branch.

**Query Parameters:**
- `name`: New branch name
- `from_branch`: Source branch to fork from
- `nessie_url` (optional): Override Nessie URL

**Response:**
```json
{
  "type": "BRANCH",
  "name": "feature-new-table",
  "hash": "ghi789jkl012"
}
```

### Delete Branch

#### `DELETE /api/nessie/branches/{branch_name}`

Delete a branch.

**Path Parameters:**
- `branch_name`: Branch name

**Query Parameters:**
- `expected_hash`: Expected hash for optimistic locking
- `nessie_url` (optional): Override Nessie URL

**Response:**
```json
{
  "success": true
}
```

### Merge Branches

#### `POST /api/nessie/merge`

Merge branches.

**Query Parameters:**
- `from_branch`: Source branch
- `into_branch`: Target branch
- `message` (optional): Merge commit message
- `nessie_url` (optional): Override Nessie URL

**Response:**
```json
{
  "success": true,
  "hash": "merged-hash-123"
}
```

## Data Quality

Base path: `/api/quality`

### Quality Overview

#### `GET /api/quality/overview`

Get overview of all quality metrics.

**Query Parameters:**
- `dagster_url` (optional): Override Dagster GraphQL URL

**Response:**
```json
{
  "total_checks": 50,
  "passing_checks": 45,
  "failing_checks": 3,
  "warning_checks": 2,
  "quality_score": 94,
  "by_category": [
    {
      "category": "Contract (Pandera)",
      "passing": 20,
      "total": 22,
      "percentage": 91
    },
    {
      "category": "dbt tests",
      "passing": 15,
      "total": 15,
      "percentage": 100
    },
    {
      "category": "Custom",
      "passing": 10,
      "total": 13,
      "percentage": 77
    }
  ],
  "recent_executions": [...],
  "failing_checks_list": [...]
}
```

### Asset Quality Checks

#### `GET /api/quality/assets/{asset_key_path}/checks`

Get quality checks for a specific asset.

**Path Parameters:**
- `asset_key_path`: Asset key path

**Query Parameters:**
- `dagster_url` (optional): Override Dagster GraphQL URL

**Response:**
```json
[
  {
    "name": "pandera_contract",
    "asset_key": ["dlt_glucose_entries"],
    "description": "Validate schema contract",
    "severity": "ERROR",
    "status": "PASSED",
    "last_execution_time": "2025-01-15T10:00:00Z",
    "last_result": {
      "passed": true,
      "metadata": {
        "rows_validated": 288
      }
    }
  }
]
```

### Check History

#### `GET /api/quality/assets/{asset_key_path}/checks/{check_name}/history`

Get execution history for a specific check.

**Path Parameters:**
- `asset_key_path`: Asset key path
- `check_name`: Check name

**Query Parameters:**
- `limit` (default: 20, max: 100): Number of executions
- `dagster_url` (optional): Override Dagster GraphQL URL

**Response:**
```json
[
  {
    "timestamp": "2025-01-15T10:00:00Z",
    "passed": true,
    "run_id": "abc-123",
    "metadata": {
      "rows_validated": 288
    }
  }
]
```

### Failing Checks

#### `GET /api/quality/failing`

Get all currently failing checks.

**Query Parameters:**
- `dagster_url` (optional): Override Dagster GraphQL URL

**Response:**
Same as quality checks list, filtered to failing only.

## Logging (Loki)

Base path: `/api/loki`

### Connection

#### `GET /api/loki/connection`

Check if Loki is reachable.

**Query Parameters:**
- `loki_url` (optional): Override Loki URL

**Response:**
```json
{
  "connected": true,
  "version": "2.9.0"
}
```

### Query Logs

#### `GET /api/loki/query`

Query logs with filters.

**Query Parameters:**
- `start`: Start time (ISO 8601)
- `end`: End time (ISO 8601)
- `run_id` (optional): Filter by Dagster run ID
- `asset_key` (optional): Filter by asset key
- `job` (optional): Filter by job name
- `partition_key` (optional): Filter by partition key
- `check_name` (optional): Filter by check name
- `level` (optional): Log level (debug, info, warn, error)
- `service` (optional): Filter by service/container name
- `limit` (default: 100, max: 1000): Number of log entries
- `loki_url` (optional): Override Loki URL

**Response:**
```json
{
  "entries": [
    {
      "timestamp": "2025-01-15T10:00:00Z",
      "level": "info",
      "message": "Materializing asset dlt_glucose_entries",
      "metadata": {
        "run_id": "abc-123",
        "asset_key": "dlt_glucose_entries",
        "partition_key": "2025-01-15"
      }
    }
  ],
  "has_more": false
}
```

### Run Logs

#### `GET /api/loki/runs/{run_id}`

Query logs for a specific Dagster run (last 24 hours).

**Path Parameters:**
- `run_id`: Dagster run ID

**Query Parameters:**
- `level` (optional): Log level filter
- `limit` (default: 500, max: 2000): Number of entries
- `loki_url` (optional): Override Loki URL

**Response:**
Same as query logs.

### Asset Logs

#### `GET /api/loki/assets/{asset_key}`

Query logs for a specific asset.

**Path Parameters:**
- `asset_key`: Asset key path

**Query Parameters:**
- `partition_key` (optional): Filter by partition
- `level` (optional): Log level filter
- `hours_back` (default: 24, max: 168): Time window in hours
- `limit` (default: 200, max: 1000): Number of entries
- `loki_url` (optional): Override Loki URL

**Response:**
Same as query logs.

### Log Labels

#### `GET /api/loki/labels`

Get available log labels for filtering.

**Query Parameters:**
- `loki_url` (optional): Override Loki URL

**Response:**
```json
{
  "labels": ["container", "namespace", "pod"]
}
```

## Row Lineage

Base path: `/api/lineage`

### Get Row Lineage

#### `GET /api/lineage/rows/{row_id}`

Get lineage info for a single row.

**Path Parameters:**
- `row_id`: Row ID from `_phlo_row_id` column

**Response:**
```json
{
  "row_id": "abc-123",
  "table_name": "fct_glucose_readings",
  "source_type": "transformation",
  "parent_row_ids": ["def-456", "ghi-789"],
  "created_at": "2025-01-15T10:00:00Z"
}
```

### Get Ancestors

#### `GET /api/lineage/rows/{row_id}/ancestors`

Get all ancestor rows (recursive).

**Path Parameters:**
- `row_id`: Row ID

**Query Parameters:**
- `max_depth` (default: 10, max: 50): Maximum recursion depth

**Response:**
```json
[
  {
    "row_id": "def-456",
    "table_name": "stg_entries",
    "source_type": "staging",
    "parent_row_ids": ["xyz-111"],
    "created_at": "2025-01-15T09:00:00Z"
  }
]
```

### Get Descendants

#### `GET /api/lineage/rows/{row_id}/descendants`

Get all descendant rows (recursive).

**Path Parameters:**
- `row_id`: Row ID

**Query Parameters:**
- `max_depth` (default: 10, max: 50): Maximum recursion depth

**Response:**
Same structure as ancestors.

### Get Lineage Journey

#### `GET /api/lineage/rows/{row_id}/journey`

Get full lineage journey (current + immediate ancestors + immediate descendants).

**Path Parameters:**
- `row_id`: Row ID

**Response:**
```json
{
  "current": {
    "row_id": "abc-123",
    "table_name": "fct_glucose_readings",
    "source_type": "transformation",
    "parent_row_ids": ["def-456"],
    "created_at": "2025-01-15T10:00:00Z"
  },
  "ancestors": [...],
  "descendants": [...]
}
```

## Maintenance

Base path: `/api/maintenance`

### Maintenance Status

#### `GET /api/maintenance/status`

Get maintenance operation status from telemetry logs.

**Response:**
```json
{
  "last_updated": "2025-01-15T10:00:00Z",
  "operations": [
    {
      "operation": "expire_snapshots",
      "namespace": "iceberg",
      "ref": "main",
      "status": "completed",
      "completed_at": "2025-01-15T09:00:00Z",
      "duration_seconds": 123.45,
      "tables_processed": 10,
      "errors": 0,
      "snapshots_deleted": 25,
      "orphan_files": 0,
      "total_records": 1000000,
      "total_size_mb": 5120.5,
      "dry_run": false,
      "run_id": "abc-123",
      "job_name": "iceberg_maintenance"
    }
  ]
}
```

### Maintenance Metrics

#### `GET /api/maintenance/metrics`

Expose maintenance metrics in Prometheus text format.

**Response:**
```
# HELP phlo_maintenance_duration_seconds Duration of maintenance operations
# TYPE phlo_maintenance_duration_seconds gauge
phlo_maintenance_duration_seconds{operation="expire_snapshots",namespace="iceberg",ref="main"} 123.45
...
```

## Search Index

Base path: `/api/search`

### Get Search Index

#### `GET /api/search/index`

Get unified search index (assets + tables + columns).

**Query Parameters:**
- `dagster_url` (optional): Override Dagster GraphQL URL
- `trino_url` (optional): Override Trino URL
- `catalog` (default: "iceberg"): Catalog name
- `branch` (default: "main"): Branch name
- `include_columns` (default: true): Include column metadata

**Response:**
```json
{
  "assets": [
    {
      "id": "asset-123",
      "key_path": "dlt_glucose_entries",
      "group_name": "nightscout",
      "compute_kind": "dlt"
    }
  ],
  "tables": [
    {
      "catalog": "iceberg",
      "schema_name": "bronze",
      "name": "dlt_entries",
      "full_name": "\"iceberg\".\"bronze\".\"dlt_entries\"",
      "layer": "bronze"
    }
  ],
  "columns": [
    {
      "table_name": "dlt_entries",
      "table_schema": "bronze",
      "name": "id",
      "type": "varchar"
    }
  ],
  "last_updated": "2025-01-15T10:00:00Z"
}
```

## Error Responses

All endpoints return errors in a consistent format:

```json
{
  "error": "Error message describing what went wrong"
}
```

For Trino query errors:

```json
{
  "ok": false,
  "error": "Query timed out",
  "kind": "timeout"
}
```

**Error kinds:**
- `timeout`: Query exceeded timeout
- `trino`: Trino execution error
- `validation`: Query validation failed (read-only mode)

## Environment Variables

Configure the API using these environment variables:

| Variable                 | Default                           | Description                    |
| ------------------------ | --------------------------------- | ------------------------------ |
| `PHLO_API_PORT`          | `4000`                            | API server port                |
| `HOST`                   | `0.0.0.0`                         | API server host                |
| `PHLO_AUTHORIZATION_BACKEND` | unset                         | Authorization backend capability name |
| `PHLO_AUTHORIZATION_MODE` | `required` in production, `optional` otherwise | Guard behavior when no authorization backend exists |
| `TRINO_URL`              | `http://trino:8080`               | Trino HTTP API URL             |
| `DAGSTER_GRAPHQL_URL`    | `http://dagster:3000/graphql`     | Dagster GraphQL endpoint       |
| `NESSIE_URL`             | `http://nessie:19120/api/v2`      | Nessie REST API URL            |
| `LOKI_URL`               | `http://loki:3100`                | Loki query API URL             |
| `PHLO_LINEAGE_DB_URL`    | (from Dagster Postgres)           | Lineage database connection    |
| `PHLO_PROJECT_PATH`      | `/app/project`                    | Path to phlo.yaml project root |

`PHLO_AUTHORIZATION_MODE=optional` leaves guarded routes open until an authorization
backend is configured. `PHLO_AUTHORIZATION_MODE=required` makes those guarded routes
return HTTP `503` when `PHLO_AUTHORIZATION_BACKEND` is unavailable.

Production (`PHLO_ENVIRONMENT=production`, `prod`, `staging`, or `regulated`,
or any regulated deployment) defaults the unset mode to `required` and fails
startup if `optional` is configured explicitly. Development remains opt-in
unless the mode is set or regulated mode is enabled. Production requires an
authentication provider and an authorization backend; `phlo services preflight
--production` verifies the locally inspectable part of that contract.

These settings may also be declared in `phlo.yaml` under `api.authorization` or
`services.phlo-api.authorization`.

## OpenAPI Documentation

Interactive API documentation is available at:

- **Swagger UI**: http://localhost:4000/docs
- **ReDoc**: http://localhost:4000/redoc
- **OpenAPI JSON**: http://localhost:4000/openapi.json
