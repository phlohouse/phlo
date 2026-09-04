# ADR 0039: dbt Project Under Workflows

## Status

**Proposed**

## Context

Phlo currently keeps dbt projects in `transforms/dbt`, while Python workflows live under
`workflows/`. This split adds mental overhead in early-stage projects and makes it easy to lose
context when switching between ingestion/quality assets and transformations. We also want to align
with the repo convention that project-specific assets live under `workflows/`.

Today, the Dagster services mount `workflows/` read-only. That prevents dbt from writing build
artifacts (e.g., `target/` and `dbt_packages/`) if we move the dbt project under `workflows/`.
We do not need backward compatibility for the old location.

## Decision

Place the dbt project at `workflows/transforms/dbt` and make the `workflows/` volume writable in
services so dbt can write artifacts in-place. Update defaults and documentation to treat this as
the only supported layout.

## Implementation

- Change configuration defaults to `workflows/transforms/dbt` and derive manifest/catalog paths
  from `dbt_project_dir`.
- Update dbt discovery to search `workflows/transforms/dbt`, and ensure hooks respect
  `DBT_PROJECT_DIR`.
- Update Dagster service mounts to make `workflows/` writable and point `DBT_PROJECT_DIR` at the
  new location.
- Update scaffolded project structure and docs to match the new layout.
- Document migration steps for existing projects (manual move of dbt project).

## Consequences

### Positive

- dbt models live next to ingestion and quality workflows, reducing project sprawl.
- Single, consistent source of truth for project assets.
- Fewer path mismatches between config, docs, and runtime.

### Negative

- Breaking change for existing projects using `transforms/dbt`.
- `workflows/` becomes writable in services, which increases the risk of accidental edits.

## Verification

- Scaffold a new project and confirm dbt artifacts are generated under
  `workflows/transforms/dbt/target`.
- Start services and run `dbt compile` via Dagster hooks without path errors.
