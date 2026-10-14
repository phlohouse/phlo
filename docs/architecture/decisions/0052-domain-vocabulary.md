# ADR 0052: Domain vocabulary

- **Status:** Accepted
- **Date:** 2026-10-10
- **Issue:** #1020 (glossary part)

## Context

Phlo uses table, asset, Dataset, workflow, job, run, and pipeline side by side,
and nothing written says whether they are aliases, containment relationships,
provider representations, or lifecycle stages. Examples in the code today:

- `@phlo.publish(table=...)` produces an `AssetSpec` tagged with the kinds
  `publish` and `dataset` (`src/phlo/flow.py`), so one declaration names a
  table, an asset, and a Dataset.
- `ObservatoryDatasetProfile` exposes `dataset`, an optional `asset`, a list
  of `tables`, and a `pipeline`
  (`packages/phlo-api/src/phlo_api/observatory_api/observatory_models.py`).
- `ObservatoryDatasetPipeline` is a read model of one Dataset's production
  stages, while the Observatory **Pipelines** route (`/pipelines/$jobName`)
  lists orchestrator jobs backed by `/api/v1/jobs`. The same word names two
  unrelated things.
- "Workflow" names authored workflow modules, Dataset workflow state
  (`claimed → review → promoted` or `rejected`), and Nessie branch workflows in the API.

Readers, including issue #957, cannot tell how these parts fit together.

## Decision

Adopt the [domain glossary](../../concepts/glossary.md) as the single
definition of Phlo's core nouns:

| Term | Meaning |
| --- | --- |
| table | A physical storage object in a catalog namespace and branch |
| asset | An executable lineage node, described by an `AssetSpec` |
| Dataset | A governed, published product backed by a table, owned by core (ADR 0051) |
| workflow | Authored workflow modules that declare assets |
| job | An orchestrator definition that selects assets to execute together |
| run | One execution of a job or asset selection |
| operation | A recorded platform action that is not a data run |
| pipeline | Presentation only; not a domain object |

Containment: a project contains workflow modules, which declare assets. An
asset usually writes one table. Jobs select assets, and each execution of a
job is a run. A Dataset is identified by a table (`candidate:<table_id>`
before promotion, `<table_id>` after) and adds governance state to it.

Rules for new code and docs:

- Use the glossary term in the glossary sense. Qualify the overloaded words
  ("publish internally", "Dataset workflow state", "branch workflow").
- Do not add new types, fields, or routes named "pipeline". Say job, run, or
  production stages.
- Write "Dataset" with a capital D for the governed product and "table" for
  storage.

## Consequences

- Reviewers have one page to check names against.
- The Jobs presentation change preserves existing route contracts. Dataset
  production models and fields use canonical names, with old names deprecated
  until 0.19.0 under the public-name process from #997. The
  [compatibility inventory](../../reference/public-name-compatibility.md)
  records each replacement and the provider or persisted contracts retained
  deliberately. Production stages do not become jobs or publication state.
- [Service topology ownership](../service-topology.md#typed-authority-and-projections)
  records the other half of #1020. Existing package manifests feed the typed
  `ServiceDefinition` authority and checked generated projections, not a
  competing central manifest.
