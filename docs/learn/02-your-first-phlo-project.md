# Your first Phlo project

A Phlo project is a small set of workflow files surrounded by generated runtime state. This post shows what the `csv-batch` template creates, why services run in containers, and how one decorated function becomes a Dagster asset without repeating the full first-pipeline tutorial.

## The problem from first principles

A data project needs two kinds of files. You own the instructions that describe what data to read and what result to produce. The platform owns the machinery that starts services, connects them, records runs, and keeps generated state consistent.

Mixing those concerns makes a project difficult to move or review. If every project contains its own orchestration server, table service, query engine, and generated configuration, changing one service becomes a manual rewrite. A project template gives you a known starting boundary instead.

Containers provide another boundary. A database, object store, catalogue, query engine, and orchestrator each have their own process and storage. Running them as services gives the project predictable names and network connections. You still own the workflow code, but you do not need to install every infrastructure process directly on your host.

This separation also makes the tutorial reproducible. A new contributor can inspect the template, install the project, initialise the generated runtime, and see the same service names. The workflow remains ordinary Python, so you can read it before you learn every detail of the platform. The runtime files describe how that Python code reaches Dagster, the catalogue, the object store, and the query engine.

This separation also makes the tutorial reproducible. A new contributor can inspect the template, install the project, initialise the generated runtime, and see the same service names. The workflow remains ordinary Python, so you can read it before you learn every detail of the platform. The runtime files describe how that Python code reaches Dagster, the catalogue, the object store, and the query engine.

## How Phlo approaches it

`phlo init` renders a project from a template. The `csv-batch` template includes `phlo`, `phlo-dlt`, and `phlo-pandera`, a small CSV file, an ingestion workflow, and a Pandera schema. The generated `pyproject.toml` makes the `workflows` package importable by the runtime.

The project layout separates source material from generated state:

```text
my-lakehouse/
├── data/events.csv
├── phlo.yaml
├── pyproject.toml
└── workflows/
    ├── ingestion/csv/events.py
    └── schemas/csv.py
```

The workflow module contains a decorated function. In the template, `@phlo.ingest.dlt` declares a partitioned asset named `dlt_events`. The function reads `data/events.csv`, adds the partition date to each event identifier, and returns a dlt resource. The schema in `workflows/schemas/csv.py` validates the rows.

The decorator is the important boundary in this first project. The function describes how to obtain rows. The decorator supplies the asset identity, partition behaviour, validation integration, and connection to the execution graph. You can therefore read the function as source logic and inspect the asset as a platform object. That distinction becomes useful when later posts add a dbt model or a quality check without turning the ingestion function into one large script.

The decorator is the important boundary in this first project. The function describes how to obtain rows. The decorator supplies the asset identity, partition behaviour, validation integration, and connection to the execution graph. You can therefore read the function as source logic and inspect the asset as a platform object. That distinction becomes useful when later posts add a dbt model or a quality check without turning the ingestion function into one large script.

`phlo services init` writes generated files under `.phlo/`. `phlo services start` starts the local services. The default stack exposes Dagster at `http://localhost:10006`. You can inspect service health with `phlo services status` and local setup diagnostics with `phlo doctor`.

Dagster is the normal execution path. `phlo materialize` launches the asset run in the Dagster container, and the Dagster UI launches the same kind of run from the asset graph. The run records its partition, logs, checks, and materialisation state.

## Try it

If you have not created the project, use the complete [Build your first pipeline](../getting-started/first-pipeline.md) tutorial. It includes the stack startup and table verification steps. The short sequence below shows the project-specific commands and the safe dry run.

Create a project and install its workflow package:

```bash
phlo init my-lakehouse --template csv-batch
cd my-lakehouse
uv pip install -e .
```

Generate and start the local runtime:

```bash
phlo services init
phlo services start
phlo services status
phlo doctor
```

Inspect what Dagster would launch for the completed partition before executing it:

```bash
phlo materialize dlt_events --partition 2025-01-15 --dry-run
```

The dry run shows the command without launching a run. When the services are healthy, remove `--dry-run` to launch the Dagster run:

```bash
phlo materialize dlt_events --partition 2025-01-15
```

Open `http://localhost:10006` and select the `dlt_events` asset. The asset graph shows the asset, and the Runs view shows the run and its step log after materialisation.

## Mental model to keep

- Your project owns `workflows/`, `data/`, and the declarations in `phlo.yaml`.
- Generated runtime state belongs under `.phlo/`.
- Templates provide packages and files, not a replacement for understanding the workflow.
- Containers provide infrastructure boundaries for local development.
- Dagster records the run that connects an asset, partition, log, and result.

## Where this goes wrong

- **The workflow package is not installed.** Dagster cannot import the project when `uv pip install -e .` has not run from the project root.
- **The generated stack is not ready.** `phlo services status` and `phlo doctor` identify unhealthy or missing services before you investigate the asset.
- **You choose today's partition.** The daily partition remains open, so use a completed date such as `2025-01-15`.
- **You inspect only container logs.** Start with the Dagster run because it links the asset, partition, step, and check result.
- **You edit generated files as source.** Keep workflow changes in the project root and regenerate runtime files when the stack definition changes.

## Next

Continue with [Ingestion with dlt](03-ingestion-with-dlt.md) to understand how the template turns source records into a partitioned table. The complete command sequence remains in [Build your first pipeline](../getting-started/first-pipeline.md), and [Project layout](../reference/project-layout.md) explains generated files.
