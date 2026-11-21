# phlo-delta

Delta Lake table-store integration for Phlo.

## Description

Provides Delta Lake table-store resources using the `deltalake` (delta-rs) Python library. Enables ACID transactions, schema evolution, and time travel on the data lakehouse.

## Installation

```bash
uv add phlo-delta
# or
phlo plugin install delta
```

## Configuration

| Variable                      | Required | Default                        | Description                      |
| ----------------------------- | -------- | ------------------------------ | -------------------------------- |
| `DELTA_WAREHOUSE_PATH`        | Yes      | `s3://lake/warehouse/delta`    | S3 path for Delta tables         |
| `DELTA_STAGING_PATH`          | No       | `s3://lake/stage`              | S3 path for staging              |
| `DELTA_DEFAULT_NAMESPACE`     | No       | `raw`                          | Default namespace/schema         |
| `DELTA_S3_ENDPOINT`           | No       | `http://minio:10001`           | S3 endpoint URL for Delta I/O   |
| `DELTA_S3_ALLOW_UNSAFE_RENAME`| No       | `true`                         | Allow unsafe rename for S3       |

> **S3 Access**: Configure AWS credentials via `~/.aws/credentials` or `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` env vars. When using MinIO, these are set automatically.

## Auto-Configuration

Works out-of-the-box when MinIO is running:

| Feature                  | How It Works                                                  |
| ------------------------ | ------------------------------------------------------------- |
| **Resource Provider**    | `DeltaResource` published as runtime resource `table_store`   |
| **Table Store Capability** | Registers `table_store:delta` capability                    |
| **Schema Migration**     | Registers `schema_migrator:delta` capability                  |

## Usage

### Resource Usage

```python
from phlo_delta.resource import DeltaResource

delta = DeltaResource()
dt = delta.get_table("bronze.users")
df = dt.to_pandas()
```

### Direct Usage

```python
from phlo_delta.settings import get_settings

opts = get_settings().get_storage_options()
# Use opts with deltalake
```

## Entry Points

- `phlo.plugins.resources` — Provides `DeltaResourceProvider`
