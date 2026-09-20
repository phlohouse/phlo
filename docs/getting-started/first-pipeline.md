# Build your first pipeline

In this tutorial we create a Phlo project from the `csv-batch` template, start the local stack, materialise one partition of a CSV ingestion asset, and read the resulting Iceberg table back from the catalog. It takes about ten minutes, most of it waiting for containers to start.

Before you start, [install Phlo](install.md) and make sure Docker is running.

## Create the project

Run `phlo init` with the `csv-batch` template and move into the new directory:

```bash
phlo init my-lakehouse --template csv-batch
cd my-lakehouse
uv pip install -e .
```

`phlo init` writes the files you own. `uv pip install -e .` installs the project so the orchestrator can import your `workflows` package. The files that matter for this tutorial are:

```text
my-lakehouse/
├── data/events.csv
├── phlo.yaml
├── pyproject.toml
└── workflows/
    ├── ingestion/csv/events.py
    └── schemas/csv.py
```

[Project layout](../reference/project-layout.md) lists the rest.

## Read the asset

Open `workflows/ingestion/csv/events.py`:

```python
from __future__ import annotations

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

One decorator turns the function into a partitioned ingestion asset. Phlo validates each row against `EventsSchema` in `workflows/schemas/csv.py`, merges rows on `event_id`, and writes the table `events`. We do not need to touch Dagster, dlt, or Iceberg configuration to run it.

## Start the local stack

Generate the runtime configuration, then start the services:

```bash
phlo services init
phlo services start
```

`phlo services init` writes `.phlo/docker-compose.yml`, `.phlo/.env`, and `.phlo/.env.local`. These files are generated state. Your source stays in the project root.

The first start pulls images and can take a few minutes. When it returns, check that everything is healthy:

```bash
phlo services status
phlo doctor
```

`phlo services status` prints one row per service. Every `STATUS` should start with `Up`, and the services with health checks should read `(healthy)`:

```text
SERVICE          STATUS                    PORTS
dagster          Up 25 seconds (healthy)   0.0.0.0:10006->3000/tcp
dagster-daemon   Up 6 seconds              3000/tcp
minio            Up 48 seconds (healthy)   0.0.0.0:10001->9000/tcp, 0.0.0.0:10002->9001/tcp
nessie           Up 36 seconds (healthy)   0.0.0.0:10003->19120/tcp
postgres         Up 47 seconds (healthy)   0.0.0.0:10000->5432/tcp
trino            Up 31 seconds (healthy)   0.0.0.0:10005->8080/tcp
```

`phlo doctor` ends with a summary line such as `Summary: 14 ok, 0 warnings, 0 failures, 0 skipped` and exits with status 0 when every diagnostic passes. If a check fails, [Monitor and debug](../guides/monitor-and-debug.md) covers the common causes.

Open Dagster at `http://localhost:10006`. The asset graph shows one asset named `dlt_events`. Nothing has run yet.

## Materialise a partition

Ask the orchestrator to run the asset for one day:

```bash
phlo materialize dlt_events --partition 2025-01-15
```

The partition must be a completed day. Today's partition is still open, so the default daily partition set does not accept it.

The command streams the run log and ends with `Successfully materialized dlt_events`. In Dagster, the `dlt_events` asset now shows a green materialisation for `2025-01-15`.

## Read the table back

List the tables that the run created in the Nessie catalog:

```bash
phlo catalog tables
```

The output lists one table, `raw.events`. The `raw` namespace is where ingestion assets land. Inspect it:

```bash
phlo catalog describe raw.events
phlo catalog history raw.events
```

`describe` shows the Iceberg schema, which matches `EventsSchema` plus the partition column. `history` shows one snapshot, written by the run you launched.

To query the rows, open a Trino shell on the `iceberg` catalog and select from the table:

```bash
phlo trino --catalog iceberg
```

```sql
SELECT event_id, name, value FROM raw.events;
```

Two rows come back:

```text
"1-2025-01-15","alpha","10"
"2-2025-01-15","beta","20"
```

`data/events.csv` has two records, and the asset appends the partition date to each id. Type `quit` to leave the shell.

## Stop the stack

When you are done, stop the containers. Data volumes survive a stop:

```bash
phlo services stop
```

To delete the data as well, add `--volumes`.

## What you built

You have a project that Phlo can run end to end: a decorated Python function, a Pandera schema, a generated Docker stack, a Dagster run, and an Iceberg table on a Nessie catalog. Everything the run produced sits under `.phlo/` or in the containers. Everything you wrote sits in `workflows/`. The `tests/` directory is empty. [Test a project](../guides/test-a-project.md) shows how to fill it and run `phlo test`.

## Where to go next

- [Ingest data](../guides/ingest-data.md) shows how to pull from a REST API or replicate a database instead of reading a CSV.
- [Add quality checks](../guides/add-quality-checks.md) shows how to block a bad partition before it is published.
- [How Phlo works](../concepts/how-phlo-works.md) explains what happened between `phlo materialize` and the table in the catalog.
