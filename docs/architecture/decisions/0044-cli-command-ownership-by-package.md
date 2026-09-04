# ADR 0044: CLI Command Ownership by Package

## Status

**Proposed**

## Context

CLI refactor underway. Core CLI should be glue only. Current ownership split uneven.
No backwards-compat shims desired; remove shims after moves.

Findings from CLI scan:

Core CLI still holds package-specific behavior:
- `src/phlo/cli/main.py`: `dev` command is Dagster-specific. Init scaffolding embeds dbt/Trino/Nessie details (`.sqlfluff`, `dbt_project.yml`, secrets).
- `src/phlo/cli/commands/workflow/create.py` + `src/phlo/cli/scaffold.py`: ingestion scaffold uses `phlo_dlt` + Pandera; belongs to ingestion/quality packages.
- `src/phlo/cli/infrastructure/utils.py` + `src/phlo/cli/infrastructure/containers.py`: Dagster container naming and lookup; used by `phlo-dagster` and `phlo-dbt` via shim.
- `src/phlo/cli/_services/*`: compatibility shims, still referenced by packages.
- `src/phlo/cli/commands/plugin/utils.py`: catalog plugin template hardcodes `targets = ["trino"]`.

Packages already ship CLI plugins:
- `phlo-nessie`: `cli_branch.py`, `cli_catalog.py`
- `phlo-dagster`: `cli_backfill.py`, `cli_materialize.py`, `cli_status.py`, `cli_logs.py`
- `phlo-dbt`: `cli_publishing.py`
- `phlo-pandera`: `cli_validate.py`, `cli_schema.py`
- `phlo-alerting`, `phlo-lineage`, `phlo-postgrest`, `phlo-hasura`, `phlo-openmetadata`

Gap:
- `phlo-trino` has no CLI module; core only carries Trino-specific scaffolding.

## Decision

Move CLI logic into owning packages:

- Dagster
  - Move `phlo dev` from `src/phlo/cli/main.py` to `packages/phlo-dagster` CLI plugin.
  - Move Dagster container helpers from `src/phlo/cli/infrastructure/utils.py` + `containers.py` into `packages/phlo-dagster` helper module.
  - Update `phlo-dbt` imports to new helper module.

- Ingestion + Quality
  - Move workflow scaffold command from `src/phlo/cli/commands/workflow/create.py` into `packages/phlo-dlt`.
  - Move Pandera schema/test scaffolding pieces from `src/phlo/cli/scaffold.py` into `packages/phlo-pandera` (or shared scaffold helper).

- Init templates
  - Move dbt project + `.sqlfluff` generation into `packages/phlo-dbt`.
  - Move service secret/env defaults into respective service packages (Nessie, Trino, etc).

- Trino
  - Add `phlo-trino` CLI plugin if Trino-specific commands needed; otherwise none.

Core CLI keeps:
- CLI entry point + plugin discovery + registration.
- `phlo services` orchestration and package-agnostic infra helpers.

## Consequences

Positive:
- Package ownership clear; less cross-package imports from core CLI.
- Service-specific behavior co-located with service code.
- Easier testing per package.

Negative:
- Import churn; requires shim updates or removals.
- Docs and tests need updates for command locations.
- Removal of shims is breaking; no fallback imports.

## Alternatives Considered

- Keep commands in core: rejected; violates package ownership.
- New shared CLI utils package: deferred; adds complexity now.
- Keep shims: rejected; explicit requirement to remove.

## Related

- ADR 0007: CLI Services Architecture
- ADR 0043: Core Package Restructuring
