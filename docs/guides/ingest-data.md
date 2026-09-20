# Ingest data

This guide shows how to add an ingestion asset that pulls records from a REST API into an Iceberg table, validates them, and runs on a schedule. The same pattern applies to any source that dlt can read: files, databases, or Python objects.

## Before you start

- You have a project created with `phlo init`, and its local stack starts with `phlo services start`. [Your first pipeline](../getting-started/first-pipeline.md) walks through this.
- `phlo-dlt` and `phlo-pandera` are installed. Both are part of `phlo[defaults]`.
- You know the shape of the records you want to load.

## 1. Declare the schema

Create `workflows/schemas/users.py`. The schema is a Pandera `DataFrameModel`. Phlo validates every batch against it and derives the Iceberg table schema from it.

```python
from __future__ import annotations

import pandera.pandas as pa


class UserSchema(pa.DataFrameModel):
    """Users pulled from the accounts API."""

    id: int
    email: str
    created_at: str
    plan: str = pa.Field(isin=["free", "team", "enterprise"])
```

Field constraints such as `isin` become validation checks. A row that fails a check fails the run, because `strict_validation` defaults to `True`.

## 2. Write the asset

Create `workflows/ingestion/api/users.py`. The decorated function receives the partition date and returns anything dlt can load. Here it returns a dlt `rest_api` source.

```python
from __future__ import annotations

import phlo
from dlt.sources.rest_api import rest_api

from workflows.schemas.users import UserSchema


@phlo.ingest.dlt(
    table_name="users",
    unique_key="id",
    group="api",
    validation_schema=UserSchema,
    cron="0 */6 * * *",
    freshness_hours=(6, 24),
)
def api_users(partition_date: str):
    return rest_api(
        client={"base_url": "https://api.example.com"},
        resources=[
            {
                "name": "users",
                "endpoint": {"path": "/users", "params": {"updated_on": partition_date}},
            }
        ],
    )
```

What each argument does:

| Argument | Effect |
| --- | --- |
| `table_name="users"` | Names the table `users` and the Dagster asset `dlt_users`. |
| `unique_key="id"` | Rows with the same `id` are upserted. The column must exist in the schema. |
| `group="api"` | Groups the asset in the Dagster UI. It does not affect the table namespace. |
| `validation_schema=UserSchema` | Validates each batch and derives the table schema. |
| `cron="0 */6 * * *"` | Creates a Dagster schedule that runs every six hours. |
| `freshness_hours=(6, 24)` | Warns when the table is older than 6 hours and fails freshness checks after 24. |

The table lands in the `raw` namespace by default. Set `DLT_DEFAULT_NAMESPACE` in `.phlo/.env.local` to change it. The full parameter list is in [Python API](../reference/python-api.md#phloingestdlt).

## 3. Choose how rows are written

`merge_strategy` defaults to `"merge"`, which upserts on `unique_key`. Use `"append"` for immutable event streams where every row is new:

```python
@phlo.ingest.dlt(
    table_name="clicks",
    unique_key="click_id",
    group="api",
    validation_schema=ClickSchema,
    merge_strategy="append",
)
```

For reference data that has no natural partition, set `partitioned=False`. The asset runs without a partition key and the function receives an empty string:

```python
@phlo.ingest.dlt(
    table_name="countries",
    unique_key="code",
    group="reference",
    validation_schema=CountrySchema,
    partitioned=False,
)
def countries(partition_date: str):
    return load_country_list()
```

## 4. Run the asset once

Start the stack if it is not running, then materialise one partition:

```bash
phlo services start
phlo materialize dlt_users --partition 2025-01-15
```

The command streams the run log and ends with `Successfully materialized dlt_users`. If validation fails, the log lists the failing column and check, and the table is not updated.

## 5. Verify the table

```bash
phlo catalog describe raw.users
phlo catalog history raw.users
```

`describe` shows the columns from `UserSchema` plus four metadata columns that Phlo adds to every ingestion table: `_phlo_row_id`, `_phlo_ingested_at`, `_phlo_partition_date`, and `_phlo_run_id`. `history` shows one snapshot per successful run.

To see the rows:

```bash
phlo trino --catalog iceberg
```

```sql
SELECT id, email, plan, _phlo_partition_date FROM raw.users LIMIT 10;
```

## Load older partitions

To load a range of dates, use `phlo backfill` instead of running `materialize` in a loop:

```bash
phlo backfill dlt_users --start-date 2025-01-01 --end-date 2025-01-14 --parallel 4
```

## Ingest from other sources

- **Files.** Read the file inside the function and return a `dlt.resource` of rows. The `csv-batch` template in [Your first pipeline](../getting-started/first-pipeline.md) shows this.
- **Databases.** Use `@phlo.ingest.sling` from `phlo-sling` for table replication with `full-refresh` or `incremental` modes. `phlo init --template sling-replication` generates a working example.
- **Streams.** `phlo-kafka` and `phlo-airbyte` add provider-specific assets. [Packages](../reference/packages.md) lists what each package contributes.

## Related

- [Add quality checks](add-quality-checks.md) for rules beyond the schema.
- [Write-audit-publish](../concepts/write-audit-publish.md) explains how failed validation keeps bad data out of published tables.
