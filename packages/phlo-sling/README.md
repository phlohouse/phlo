# phlo-sling

Sling-based database replication ingestion provider for Phlo.

## Overview

`phlo-sling` wraps [Sling](https://slingdata.io/) as a complementary ingestion engine alongside `phlo-dlt`. Sling is a data movement CLI (DB→DB, File→DB, DB→File) with 30+ connectors, optimized for high-speed database replication.

Where DLT excels at API-based ingestion with schema evolution and normalisation, Sling excels at raw-speed database replication with wildcard stream selection (`my_schema.*`), incremental modes, and direct DB-to-DB transfers.

## Installation

```bash
pip install phlo-sling
```

## Usage

### Decorator-Based Replication

```python
import phlo
from phlo_sling import phlo_sling_replication


@phlo_sling_replication(
    stream_name="public.users",
    table_name="users",
    source_conn="PHLO_POSTGRES",
    target_conn="WAREHOUSE",
    group="crm",
    mode="incremental",
    primary_key="id",
    update_key="updated_at",
    cron="0 */2 * * *",
    owner="data-team",
)
def replicate_users(context):
    """Replicate users table from Postgres into raw.users on WAREHOUSE."""
    return None
```

### Python-First File Discovery

Use `phlo_sling_assets` when you want Python logic to discover folders/files
first and register one Sling-backed asset per result.

```python
from pathlib import Path

from phlo_sling import SlingReplication, phlo_sling_assets


@phlo_sling_assets(group="finance")
def discover_workbooks():
    root = Path("/mnt/finance")

    for workbook in root.rglob("*.xlsx"):
        table_name = workbook.stem.replace("-", "_").lower()
        yield SlingReplication(
            stream_name=f"file://{workbook}",
            table_name=table_name,
            source_conn="LOCAL",
            target_conn="WAREHOUSE",
            object=f"raw.{table_name}",
            mode="full-refresh",
            source_options={"sheet": "Sheet1!A:F"},
            description=f"Ingest workbook {workbook.name}",
            metadata={"workbook_path": str(workbook)},
            tags={"format": "xlsx"},
        )
```

Use the original `phlo_sling_replication` decorator when you want one stable
asset whose function may return runtime Sling overrides such as a dynamic
`src_stream` or `where` clause.

### CLI Commands

```bash
# Run replication from YAML
phlo sling run --replication replications/pg_to_lake.yaml

# Run ad-hoc replication
phlo sling run --source PHLO_POSTGRES --stream public.users --target PHLO_S3 --object raw/users.parquet

# Override the inferred destination object when needed
phlo sling run --source PHLO_POSTGRES --stream public.users --target WAREHOUSE --object raw.users

# List connections
phlo sling conns

# Discover available streams
phlo sling discover PHLO_POSTGRES
phlo sling discover PHLO_POSTGRES --schema public --format json
```

## Configuration

The following environment variables can be used to configure Sling:

- `SLING_DEFAULT_NAMESPACE` - Default namespace for generated replication table names (default: "raw")
- `SLING_DEFAULT_MODE` - Default replication mode (default: "incremental")
- `SLING_AUTO_CONNECTIONS` - Auto-generate Sling connections from Phlo capability metadata (default: true)
- `PHLO_OBJECT_STORE` - Select the active `object_store` capability when more than one is installed. MinIO is the deterministic default when no explicit choice is set (SP9-DECISION-03).

Notes:

- Decorator-backed replications need a real Sling destination. When `target_conn` is set and `object` is omitted, `phlo-sling` targets `<namespace>.<table_name>` automatically.
- If you set `SLING_AUTO_CONNECTIONS=false`, `phlo-sling` stops injecting `PHLO_POSTGRES` / `PHLO_S3` connection definitions into the environment.
- `PHLO_S3` now resolves from the active `object_store` capability instead of importing `phlo-minio` / `phlo-rustfs` directly. If both are installed, set `PHLO_OBJECT_STORE=minio` or `PHLO_OBJECT_STORE=rustfs`.
