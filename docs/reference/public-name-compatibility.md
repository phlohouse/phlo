# Public name compatibility

This inventory records the remaining naming decisions under #1020 after the
Jobs presentation change in #1099. [ADR 0052](../architecture/decisions/0052-domain-vocabulary.md)
and the [domain glossary](../concepts/glossary.md) define the terms. The
[public deprecation process](python-api.md#removal-schedule) requires a named
removal release, a replacement, and honest usage evidence.

## Dataset production compatibility until 0.19.0

Dataset production describes stages that produce a governed Dataset. It does
not identify a job, a run, or a publication transition. Publication state
remains on `dataset.publication_state`; readiness remains on `publishing`.

| Public name | Canonical name | Compatibility and removal decision |
| --- | --- | --- |
| `ObservatoryDatasetPipeline` | `ObservatoryDatasetProduction` | Python import alias warns with replacement and removal in 0.19.0. Both imports resolve to the same model. |
| `ObservatoryPipelineStage` | `ObservatoryProductionStage` | Python import alias warns; removed in 0.19.0. Stage IDs `ingest`, `transform`, `checks`, and `publish` are unchanged. |
| `ObservatoryPipelineList` | `ObservatoryDatasetProductionList` | Python import alias warns; removed in 0.19.0. The `items` envelope is unchanged. |
| `ObservatoryDatasetProfile.pipeline` | `production` | Old constructor input warns. Responses expose both names from the same value, and OpenAPI marks `pipeline` deprecated. Removed in 0.19.0. |
| Production model `last_run` | `last_operation` | The referenced object has `kind="operation"`, not a run. Old constructor input warns. Responses preserve the old name as a deprecated computed field until 0.19.0. |
| `GET /api/observatory/pipelines` | `GET /api/observatory/dataset-production` | Same protected Dataset-read action, cache, and response. OpenAPI marks the old route deprecated with removal in 0.19.0. Neither route lists orchestrator jobs. |
| Profile `sections.pipelines` | `sections.production` | Both availability keys remain until 0.19.0. Consumers must move to `production`. |
| Dataset capability metadata `profile_sections=["pipelines"]`, `read_models=["pipelines", "dataset-profile"]` | `production`, `dataset-production`, and `dataset-profile` | Retained for existing capability consumers until 0.19.0. These names describe Dataset production, not the Jobs page. |

Canonical input wins if both field names are present. Legacy response fields
are computed from canonical data, so a stale alias cannot overwrite a newer
operation or production value. Legacy fields remain readable in Python and
JSON throughout the compatibility window. Assigning a legacy Python property
warns and updates the canonical value. New Python code uses the canonical
models and fields.

`phlo.legacy.dataset_production_name.uses` counts old Python model lookups and
old-only constructor inputs or legacy property assignments, tagged by name. It does not count automatic
response serialisation. `phlo.legacy.dataset_production_route.uses` counts
requests reaching the old HTTP handler, including cache hits. Both counters
use canonical observe metrics, with value `1` per use.

The server cannot observe which JSON field a remote client reads. A zero
counter therefore does not prove that response aliases or capability metadata
have no consumers. They remain until 0.19.0 unless a release owner confirms
consumer migration separately. Disabled telemetry does not prove zero use.

## Retained contracts and distinct concepts

| Name or API term | Actual consumer and meaning | Decision |
| --- | --- | --- |
| Jobs UI `/pipelines`, `/pipelines/timeline`, `/pipelines/$jobName`, and run detail URLs | Observatory links, browser bookmarks, route parameters, and search state; backed by `/api/v1/jobs` and `/api/v1/runs` | Labels are Jobs and runs after #1099. Retain these URL contracts indefinitely; do not add a parallel route family merely to rename a path. |
| Frontend `getPipelineList`, `getPipelineJob`, component directory names, and search helpers | Internal implementation of the Jobs routes in `src/lib/data/api/pipelines.ts` | Not public domain models. Retain implementation names with the routes; do not duplicate helpers with aliases. |
| Dagster GraphQL `pipelineName`, `pipelines`, and `PipelineSelector` | Provider-native queries in the Dagster adapter and v1 jobs API | Retain exactly as Dagster defines them. V1 responses translate them to `Job.id`, `Run.job_id`, and `job_name` launch inputs. |
| Legacy `DagsterRunStatus.pipeline_name` and raw Dagster run dictionaries | The legacy `/api/dagster` provider boundary | Retain the native projection for compatibility, not as a new neutral term. New clients use `/api/v1/runs` and `job_id`. Removal requires retirement of the legacy provider API as a whole, not a field-only break under #1020. |
| `PipelineRun`, `pipeline_name`, `pipeline_run` tables and event kinds in `phlo.run_evidence` | Versioned durable evidence records, stores, hook consumers, and report identity | Retain the v1 evidence schema and public model together. It means one run, not a Dataset or a job definition. A future schema version must migrate persisted records and consumers before removal; #1020 does not invalidate existing run evidence. |
| DLT `pipeline`, `pipeline_name`, `setup_dlt_pipeline`, and provider scope events | DLT's actual load-pipeline object and its telemetry adapter | Retain provider-native concepts. They are distinct from Phlo jobs and Dataset production stages. |
| Operation kinds `pipeline`, `pipeline_run`, `pipeline.package`, dependency-activity source kinds, and WAP `pipeline-run-*` branch IDs | Existing journals, manifest evidence, telemetry, and branch references | Retain persisted identifiers and provider-native values. Read adapters classify them; do not rewrite historical evidence or branch identity to rename presentation. |
| `workflows/`, authoring APIs, `ObservatoryWorkflowProposalRequest`, and workflow wizard actions | Authored workflow modules and source proposals | Correct glossary meaning. Retain. An apply action is an operation, not a data run. |
| `ObservatoryDatasetWorkflowConfig`, Dataset workflow actions, and `workflow_state` | Dataset governance transitions | Distinct, explicitly qualified Dataset workflow state under ADR 0051. Retain. |
| `/api/v1/branch-workflows`, branch-workflow actions, and alert-workflow actions | Catalog branch checks and alert-provider procedures | Distinct qualified provider procedures. Retain; never present them as authored data workflow modules or job runs. |
| `ObservatoryTable.asset_id`, `ObservatoryDatasetProfile.dataset`, `asset`, and `tables` | Physical tables, executable lineage nodes, and governed Dataset profiles | Correct references between distinct glossary concepts. Retain, including candidate table promotion. |
| `publish_table`, `@phlo.publish`, `publishing`, and publication actions | Data-plane materialisation, declaration metadata, readiness, and governance transitions | Retain distinct contracts. Use "publish internally" or "publication transition" for Dataset state changes. Publication work remains owned by #987. |

The inventory follows the current provider-neutral models, legacy Dagster
adapter, v1 job/run contracts, workflow authoring and branch APIs, evidence
models, DLT adapter, and Observatory route consumers. There is no current
TypeScript consumer of `ObservatoryDatasetPipeline` or `profile.pipeline`;
their HTTP and Python contracts still require the compatibility window.
