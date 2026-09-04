# ADR 0045: Package-Owned Settings and CLI Extraction

## Status

**Proposed**

## Context

Core should be glue only. Audit shows package-specific settings and CLI behavior still live
in `src/phlo`, plus a few shims.

Exhaustive core items to extract/remove:

- Service configs + monolithic settings:
  - `src/phlo/config/alerting.py`
  - `src/phlo/config/catalog.py`
  - `src/phlo/config/database.py`
  - `src/phlo/config/integration.py`
  - `src/phlo/config/orchestration.py`
  - `src/phlo/config/query.py`
  - `src/phlo/config/storage.py`
  - `src/phlo/config/settings.py` service fields (postgres/minio/nessie/iceberg/trino/dagster/dbt/openmetadata/superset/alerting/lineage/observatory)

- CLI/package logic in core:
  - `src/phlo/cli/main.py` dbt scaffold writer

- Service-specific runtime data in core:
  - `src/phlo/plugins/compose/generator.py` Dagster gitignore entries

- Shims / package fallbacks:
  - `src/phlo/__init__.py` re-exports for `phlo_dlt` and `phlo_quality`
  - `src/phlo/orchestrators/selection.py` Dagster fallback import

## Decision

Move all service settings into owning packages and keep core settings minimal.
Remove shims and package fallbacks. Move CLI logic into packages or call package helpers.

Package settings ownership:

- `phlo-postgres`: `PostgresSettings` (postgres connection + marts schema)
- `phlo-minio`: `MinioSettings` (minio credentials + S3 region)
- `phlo-nessie`: `NessieSettings` (nessie host/ports/api)
- `phlo-iceberg`: `IcebergSettings` (warehouse/staging/default namespace/ref)
- `phlo-trino`: `TrinoSettings` (trino host/port/catalog)
- `phlo-dagster`: `DagsterSettings` (dagster port, workflows path, executor flags, host platform)
- `phlo-dbt`: `DbtSettings` (dbt project/manifest/catalog paths)
- `phlo-openmetadata`: `OpenMetadataSettings` (openmetadata connection + sync options)
- `phlo-superset`: `SupersetSettings` (superset port/admin credentials)
- `phlo-alerting`: `AlertingSettings` (slack/pagerduty/email)
- `phlo-lineage`: `LineageSettings` (lineage DB URL)
- `phlo-observatory`: `ObservatorySettings` (observatory settings DB URL)

Core settings keep only:
- plugin system controls
- logging controls
- orchestrator selection

CLI + runtime data ownership changes:
- Move dbt scaffold writer to `phlo-dbt` and call from core init.
- Add `gitignore` to service definitions; compose generator aggregates service entries.

No backwards compatibility shims.

## Consequences

- Breaking import changes for settings; docs/tests updated.
- Package dependencies expand (settings imports now explicit).
- Core config surface shrinks and remains stable.

## Alternatives Considered

- Keep monolithic `phlo.config` settings: rejected; violates package ownership.
- Keep shims: rejected; explicit requirement to remove.
- Create shared settings package: deferred; adds new indirection.

## Related

- ADR 0043: Core Package Restructuring
- ADR 0044: CLI Command Ownership by Package
