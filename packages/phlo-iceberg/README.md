# phlo-iceberg

Apache Iceberg catalog integration for Phlo.

## Description

Provides PyIceberg table-store resources for adapters. Enables ACID transactions, schema evolution, and time travel on the data lakehouse.

## Installation

```bash
pip install phlo-iceberg
# or
phlo plugin install iceberg
```

## Configuration

| Variable                    | Required | Default               | Description                   |
| --------------------------- | -------- | --------------------- | ----------------------------- |
| `ICEBERG_WAREHOUSE_PATH`    | Yes      | `s3://lake/warehouse` | S3 path for Iceberg warehouse |
| `ICEBERG_STAGING_PATH`      | No       | `s3://lake/stage`     | S3 path for staging           |
| `ICEBERG_DEFAULT_NAMESPACE` | No       | `raw`                 | Default namespace/schema      |
| `ICEBERG_DEFAULT_REF`       | No       | `main`                | Default catalog ref/branch    |
| `ICEBERG_CATALOG_URI`       | No       | `http://nessie:19120/iceberg` | Iceberg REST catalog URI |

> **S3 Access**: Configure AWS credentials via `~/.aws/credentials` or `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` env vars. When using MinIO, these are set automatically.

## Auto-Configuration

Works out-of-the-box when MinIO and Nessie are running:

| Feature                | How It Works                                                                     |
| ---------------------- | -------------------------------------------------------------------------------- |
| **Resource Provider**  | `IcebergResource` published as runtime resource `table_store`                    |
| **Table Store Capability** | Registers `table_store:iceberg` capability                               |
| **Schema Migration**   | Registers `schema_migrator:iceberg` capability                                   |

## Usage

### Resource Usage

```python
from phlo_iceberg.resource import IcebergResource

iceberg = IcebergResource()
catalog = iceberg.get_catalog()
table = catalog.load_table("bronze.users")
df = table.scan().to_pandas()
```

### Direct Usage

```python
from phlo_iceberg.settings import get_settings

config = get_settings().get_pyiceberg_catalog_config("main")
# Use config with pyiceberg
```

### Adopt legacy history

Use `phlo_iceberg.history_adoption.validate_history_adoption` for a read-only report, then `adopt_history_table` for explicit, revalidated transactional adoption. Ordinary history writes still reject unmarked nonempty tables. See the [adoption and recovery guide](../../docs/guides/adopt-history-table.md) before changing an existing table.

## Entry Points

- `phlo.plugins.resources` - Provides `IcebergResourceProvider`
