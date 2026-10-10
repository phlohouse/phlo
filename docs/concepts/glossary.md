# Domain glossary

Phlo's core nouns, what each one means, and how they contain or refer to each
other. Code, API models, CLI output, and Observatory labels should use these
words in these senses. [ADR 0052](../architecture/decisions/0052-domain-vocabulary.md)
records the decision; the [reference glossary](../reference/glossary.md) covers
tool names and secondary terms.

## How the nouns fit together

```text
project
└── workflow modules (workflows/)          authored code
    └── declare assets                     executable lineage nodes
        ├── each asset writes or reads tables   physical storage
        └── assets are selected by jobs         orchestrator groupings
                └── a job launches runs         one execution each

table ──promote──▶ Dataset                 governed product over a table
```

- A **project** contains workflow modules.
- A **workflow** module declares one or more **assets**.
- An **asset** usually materialises one **table**. A table can exist without
  an asset (for example, a table created outside Phlo and found in the
  catalog).
- A **job** selects a set of assets. A **run** is one execution of a job or
  an asset selection.
- A **Dataset** is identified by a table and adds governance state to it. It
  is never the table itself.

## Core terms

### Table

A physical storage object in the lakehouse: an Iceberg or Delta table in a
catalog namespace, on a catalog branch. Tables are what queries read.
Observatory models a table as `ObservatoryTable`, which carries an optional
`asset_id` naming the asset that writes it.

### Asset

An executable node in the lineage graph. Discovery turns decorators in
workflow modules into `AssetSpec` objects
(`src/phlo/capabilities/specs.py`) with a key, group, kinds, dependencies,
checks, and run behaviour. Orchestrator adapters turn those specs into
runtime definitions, such as Dagster assets. An asset key names the asset,
not the table: `@phlo.publish(table="marts.orders")` produces the asset key
`publish_marts_orders`.

### Dataset

A governed, published data product backed by a table. Core owns its identity,
workflow state, publication state, and readiness
([ADR 0051](../architecture/decisions/0051-dataset-authority.md)). Before
promotion a Dataset is a **candidate** with ID `candidate:<table_id>`; after
promotion its ID is `<table_id>`. Write "Dataset" with a capital D when you
mean this governed product, and "table" when you mean storage.

`@phlo.publish(table=...)` marks a table as a Dataset surface. The asset it
produces carries the kinds `publish` and `dataset`. The asset is the work that
publishes; the Dataset is the governed product.

### Workflow

Authored project code: the Python modules under the project's workflow path
(`workflows/` in the default template) that declare assets, checks, and their
settings (`workflows.<namespace>.settings` in `phlo.yaml`). Observatory's
workflow wizard writes new workflow modules into this path.

A workflow is a definition. It is never an execution; that is a run.

### Job

An orchestrator definition that selects a set of assets to execute together.
In the default stack these are Dagster jobs, defined in workflow modules or
shipped by packages (for example the Iceberg maintenance jobs in
`phlo-dagster`). `/api/v1/jobs` lists them by `job_id`.

### Run

One execution of a job or an asset selection, with an ID, a status, start
and end times, and the assets it touched. `phlo materialize`, `phlo backfill`,
asset `cron` schedules, and the Dagster UI all launch runs. Run evidence
([ADR 0048](../architecture/decisions/0048-blessed-run-evidence-composition.md))
is the structured record of a run.

### Operation

A recorded platform action that is not a data run, such as a WAP promotion,
table maintenance, or a backup. Operations are kept in the durable operation
journal (`src/phlo/operations/journal.py`).

### Pipeline

Not a domain object. "Pipeline" is a presentation word only, and currently
has two meanings that do not refer to the same thing:

- The Observatory **Pipelines** page (`/pipelines/$jobName`) lists jobs and
  their runs. Here "pipeline" is a UI label for a job.
- The Observatory API model `ObservatoryDatasetPipeline` is a read model of
  one Dataset's production stages (ingestion, transforms, checks,
  publishing) and its latest operation.

New code should say job, run, or Dataset production stages. Do not add new
types, fields, or routes named "pipeline".

## Words with more than one meaning

| Word | Meaning to use | Other meaning in the codebase | How to tell them apart |
| --- | --- | --- | --- |
| publish | Data-plane materialisation through a publish-target provider (`publish_table`) | The Dataset publication transition (`draft → published → retired`) | Say "publish internally" or "publication transition" for the governance change, as ADR 0051 requires |
| workflow | Authored workflow modules | Dataset workflow state (`claim → review → promote/reject`); Nessie branch workflows in the API | Say "Dataset workflow state" or "branch workflow" for the other senses |
| pipeline | None (presentation only) | UI label for jobs; Dataset production-flow read model | Say "job" or "production stages" |
| asset | Executable lineage node | The table an asset writes | Say "table" for storage |

## Related terms

- **Candidate**: a table not yet promoted to a Dataset, identified as
  `candidate:<table_id>`.
- **Branch**: a versioned catalog reference used to isolate, compare, merge, or
  publish table changes.
- **Check**: a quality validation attached to an asset, reported as a
  structured result.
- **Capability** and **provider**: a stable contract and the package component
  that fulfils it. See [plugins and capabilities](plugins-and-capabilities.md).
