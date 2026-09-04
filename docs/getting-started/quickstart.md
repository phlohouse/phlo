# 10-Minute Quickstart

Create a new Phlo project, run a generated ingestion asset, and query the local lakehouse stack.

This quickstart uses the `csv-batch` template because it is deterministic: it does not require an external API key, a public demo service, or cloning the Phlo source repository.

## What You Will Build

A local CSV ingestion pipeline that:

1. reads `data/events.csv`
2. validates rows with a Pandera schema
3. registers a DLT-backed Phlo asset
4. materializes the asset through the local Dagster runtime
5. makes the resulting table available through the project data plane

## Prerequisites

- Python 3.11 or 3.12 for the currently tested support boundary
- Docker with Compose v2, which is the currently supported path; Podman with a Compose provider is documented as experimental and is not CI-supported
- `uv`

Install Phlo with the default local stack:

```bash
uv pip install "phlo[defaults]"
```

Verify the CLI:

```bash
phlo --version
phlo init --list-templates
```

The current starter templates are:

| Template | Use it when you want |
| --- | --- |
| `minimal` | an empty project |
| `csv-batch` | a local file-backed ingestion asset |
| `api-ingestion` | a REST API ingestion asset |
| `basic` | a dbt-ready project shell |
| `dbt-medallion` | bronze, silver, and gold dbt layers |
| `sling-replication` | Sling-based replication |
| `observability-demo` | a small pipeline with telemetry wiring |

## Step 1: Create a Project

```bash
phlo init my-project --template csv-batch
cd my-project
```

The template creates the files you own:

```text
my-project/
├── data/events.csv
├── phlo.yaml
├── pyproject.toml
├── workflows/
│   ├── ingestion/csv/events.py
│   └── schemas/csv.py
└── tests/
```

Install the project dependencies in your environment:

```bash
uv pip install -e .
```

## Step 2: Inspect the Asset

Open `workflows/ingestion/csv/events.py`. The important part is the Phlo ingestion decorator:

```python
from pathlib import Path

import dlt
import pandas as pd
import phlo

from workflows.schemas.csv import EventsSchema


@phlo.ingest.dlt(
    table_name="events",
    unique_key="event_id",
    validation_schema=EventsSchema,
    group="csv",
    freshness_hours=(1, 24),
)
def csv_events(partition_date: str) -> object:
    events = pd.read_csv(Path("data/events.csv"))
    events["event_id"] = events["id"].astype(str) + "-" + partition_date
    rows = events.to_dict(orient="records")
    return dlt.resource(rows, name="events")
```

The decorator registers an asset that can be discovered by the orchestrator. The schema in `workflows/schemas/csv.py` defines the validation contract.

## Step 3: Initialize Services

Generate the local service configuration:

```bash
phlo services init
```

This creates `.phlo/docker-compose.yml`, `.phlo/.env`, and `.phlo/.env.local`. The generated files are project-local runtime state; source files stay in your project root.

Start the default stack:

```bash
phlo services start
```

Check health:

```bash
phlo services status
phlo doctor
```

By default, Dagster is available at `http://localhost:10006`.

## Step 4: Materialize the Asset

Run the generated asset for a specific partition:

```bash
phlo materialize dlt_events --partition 2025-01-15
```

Use a completed daily partition, such as yesterday. Today's partition is still open and is not selectable by the default daily partition definition.

You can also open Dagster and materialize the `dlt_events` asset from the UI.

## Step 5: Query and Inspect

Use Trino for SQL inspection:

```bash
phlo trino
```

Then query the table using the catalog and schema configured for your generated stack. For most local projects, start by listing tables:

```bash
phlo catalog tables
```

Inspect the table metadata:

```bash
phlo catalog describe raw.events
phlo catalog history raw.events
```

If your table name includes a prefix or a different namespace, use the exact value returned by `phlo catalog tables`.

## What You Just Learned

- `phlo init` creates project-owned workflow files from templates.
- `phlo services init` generates runtime infrastructure under `.phlo/`.
- `phlo services start` starts the generated stack without requiring hand-written Compose files.
- `phlo materialize` delegates execution to the configured orchestrator runtime.
- Package plugins add CLI commands, services, assets, resources, and UI surfaces as they are installed.

## Next Steps

- Build a REST ingestion asset with `phlo init my-api-project --template api-ingestion`.
- Build a transformation project with `phlo init my-dbt-project --template dbt-medallion`.
- Learn the development loop in [Developer Workflow](../guides/developer-workflow.md).
- Learn package selection in [Choosing Components](../guides/choosing-components.md).
- Keep command details close with [CLI Reference](../reference/cli-reference.md).

## Common Issues

### Services do not start

Check that your container backend is running:

```bash
docker ps
phlo services status
phlo doctor --verbose
```

Podman is a documented experimental backend and is not CI-supported. If you choose
to try it, set the backend explicitly:

```bash
export PHLO_CONTAINER_BACKEND=podman
phlo services start --backend podman
```

### The asset is not visible in Dagster

Restart the orchestration service after adding or renaming workflow files:

```bash
phlo services restart --service dagster
phlo doctor
```

### A command says a service package is missing

Install the package that owns the surface you are using. The default quickstart uses:

```bash
uv pip install "phlo[defaults]"
```

For minimal installs, add packages directly, for example:

```bash
uv pip install phlo-dagster phlo-dlt phlo-pandera phlo-iceberg phlo-trino
```
