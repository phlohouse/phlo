# Connect your first API

This tutorial scaffolds a tested REST API ingestion workflow, shows where to put authentication, and verifies the workflow before it touches the lakehouse. Use an API with a stable identifier and a time-range filter. If your API has neither, adapt the endpoint and merge strategy rather than pretending the generated defaults fit.

## Before you start

- Finish [Build your first pipeline](../getting-started/first-pipeline.md).
- Install `phlo-dlt` and `phlo-pandera`. They are included in `phlo[defaults]`.
- Obtain the API's base URL, record path, stable unique key, pagination rules, and authentication method.

## 1. Generate the workflow

From your project root, create an ingestion asset, schema, and focused test. This example expects an `id`, `name`, and `updated_at` field:

```bash
uv run phlo workflow create \
  --domain accounts \
  --table users \
  --unique-key id \
  --cron "0 */6 * * *" \
  --api-base-url "https://api.example.com/v1/" \
  --field id:int \
  --field name:str \
  --field updated_at:datetime
```

The command creates:

```text
workflows/schemas/accounts.py
workflows/ingestion/accounts/users.py
tests/test_accounts_users.py
```

It also ensures the project declares `phlo-dlt` and `phlo-pandera`. Run `uv sync` after scaffolding if those dependencies were added.

## 2. Match the endpoint contract

Open `workflows/ingestion/accounts/users.py`. The scaffold sends `start_date` and `end_date` for one daily partition and requests the `users` path. Compare those names with the API documentation.

For an API that expects `updated_after` and `updated_before`, change only the endpoint declaration:

```python
"endpoint": {
    "path": "users",
    "params": {
        "updated_after": start_time,
        "updated_before": end_time,
    },
},
```

Configure pagination and record selection in the dlt REST API source when the response wraps rows or supplies continuation links. Do not materialise until one request returns the record shape declared in `workflows/schemas/accounts.py`.

## 3. Add authentication without committing it

Store credentials in `.phlo/secrets/.env`, which is ignored by Git:

```dotenv
ACCOUNTS_API_TOKEN=replace-me
```

Read the token at runtime and add the header to the generated client:

```python
import os

token = os.environ["ACCOUNTS_API_TOKEN"]

return rest_api(
    client={
        "base_url": base_url,
        "headers": {"Authorization": f"Bearer {token}"},
    },
    resources=[
        {
            "name": "users",
            "endpoint": {
                "path": "users",
                "params": {
                    "updated_after": start_time,
                    "updated_before": end_time,
                },
            },
        }
    ],
)
```

Never put a real token in `phlo.yaml`, the workflow module, a test fixture, or command history. If the provider rotates credentials, restart the process that runs the asset so it receives the new environment.

## 4. Verify before materialising

Run the generated schema test and the static workflow check:

```bash
uv run pytest tests/test_accounts_users.py -q
uv run phlo workflow check workflows/ingestion/accounts/users.py
```

The generated test proves the schema accepts a minimal row. Add a recorded or fake HTTP response test for pagination and response-shape logic; do not make the unit suite depend on the live vendor.

## 5. Run one completed partition

Start the stack, reload Dagster after adding the module, and materialise a date for which the source data is complete:

```bash
uv run phlo services start
uv run phlo services restart --service dagster
uv run phlo materialize dlt_users --partition 2025-01-15
```

Verify both the table contract and the run:

```bash
uv run phlo catalog describe raw.users
uv run phlo catalog history raw.users
uv run phlo status --assets
```

If the run fails, inspect `uv run phlo services logs --service dagster --tail 200`. A 401 or 403 is an authentication problem; a Pandera error is a response/schema mismatch; an empty successful run usually means the time filter or record selector does not match the API.

## 6. Make retries safe

Keep `unique_key="id"` and the default merge strategy when the source identifier is stable. A retry then updates the same logical record. Use append only for immutable events with a unique event identifier. Before enabling the schedule, rerun the same partition and confirm row counts do not double.

## Related

- [Ingest data](ingest-data.md) explains the decorator, partitions, backfills, files, and database sources.
- [Test a project](test-a-project.md) adds project-level tests and CI gates.
- [Settings reference](../reference/settings.md) lists generated package settings without reading your environment.
