# Support matrix

> Generated from `registry/support/v1.json` by `scripts/generate_reference_docs.py`. Do not edit this page directly.

Release `0.19.0` has `alpha` maturity. Production readiness is `false`.

A target profile states the intended support boundary. Maturity and readiness state what is available now. Gates and blockers state what must pass before release.

## Compatibility surface inventory

`generated/compatibility-surface.json` is the checked inventory that the [ADR 0053](../architecture/decisions/0053-what-1-0-promises.md) decision 7 exit criteria require for the `blessed_core` profile: CLI command paths and parameters from `blessed_core` packages, `/api/v1` methods and paths plus `/health`, `phlo.__all__`, the covered entry-point groups and the entry points that `blessed_core` packages declare in them, hook event types and the envelope version, and the configuration and run-evidence schema versions. It excludes surfaces that decision 2 does not cover, such as legacy HTTP families, non-`blessed_core` packages, and underscore-prefixed names.

The file does not record every covered detail. HTTP schemas, status codes and authorisation actions, the CLI JSON envelope and exit status, plugin base classes, capability specifications, `service.yaml`, project layout, and the support manifest shape remain governed by their decision 2 sources of truth, so an unchanged inventory does not show that those are unchanged.

Run `make docs-reference-generate` to regenerate it with the other references. `make docs-reference-check`, which `make check` and the CI Python lane run, fails when the file is stale. Review a diff to this file as a change to a covered surface under ADR 0053 decision 4; during the decision 6 feature freeze, an addition also needs the recorded exception that decision 6 requires.

## Release claims

- Release maturity: `alpha`. The schema permits `alpha` and `supported`. `supported` is permitted only together with production readiness `true`. In a production-ready release, `blessed_core` components with target status `supported` may also report current maturity `supported`.
- Production readiness: `false`. `true` is permitted only when every required gate (`security`, `run_evidence`, `maintenance`, `upgrade_restore`, `golden_path`) is `passed` and every `blessed_core` gate component is `passed` or `not_required` (ADR 0053). A gate component is `not_required` only when no gate applies to it, and it still needs checked evidence.
- Production status: `blocked`. It is `blocked` until those gates pass, then `required_before_release` while production readiness is `false`, and `passed` once production readiness is `true`.
- Artifact pinning: `not_yet_published`. The schema permits `not_yet_published` and `published`; `published` records that the release set's artifacts are published and pinned by the release BOM (ADR 0050).
- Release version: a `current_release.version` at or above `1.0.0` is rejected unless production readiness is `true`. Following PEP 440 ordering, pre-releases and dev releases of 1.0.0, such as `1.0.0rc1`, sort below `1.0.0` and are permitted. `1.0.0.post1` and pre-releases of later versions, such as `1.1.0rc1`, are not.

## Capabilities

### `dbt_transformations`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `run_evidence`, `upgrade_restore`, `golden_path`.
- Blockers: `security`, `run_evidence`, `upgrade_restore`, `golden_path`.

### `dlt_ingestion`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `run_evidence`, `upgrade_restore`, `golden_path`.
- Blockers: `security`, `run_evidence`, `upgrade_restore`, `golden_path`.

### `golden_path_ci`

- Target profile: `blessed_core`; target status: `required`.
- Current maturity: `planned`; readiness: `blocked`.
- Applicable gates: `golden_path`.
- Blockers: `golden_path`.

### `iceberg_tables`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `run_evidence`, `maintenance`, `upgrade_restore`, `golden_path`.
- Blockers: `security`, `run_evidence`, `maintenance`, `upgrade_restore`, `golden_path`.

### `maintenance_operations`

- Target profile: `blessed_core`; target status: `required`.
- Current maturity: `planned`; readiness: `blocked`.
- Applicable gates: `security`, `maintenance`, `upgrade_restore`, `golden_path`.
- Blockers: `security`, `maintenance`, `upgrade_restore`, `golden_path`.

### `mandatory_authorization`

- Target profile: `blessed_core`; target status: `required`.
- Current maturity: `blocked`; readiness: `blocked`.
- Applicable gates: `security`, `golden_path`.
- Blockers: `security`, `golden_path`.

### `nessie_branch_write_audit_publish`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `run_evidence`, `maintenance`, `upgrade_restore`, `golden_path`.
- Blockers: `security`, `run_evidence`, `maintenance`, `upgrade_restore`, `golden_path`.

### `observatory_run_report`

- Target profile: `blessed_core`; target status: `required`.
- Current maturity: `planned`; readiness: `blocked`.
- Applicable gates: `security`, `run_evidence`, `golden_path`.
- Blockers: `security`, `run_evidence`, `golden_path`.

### `pandera_quality`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `run_evidence`, `upgrade_restore`, `golden_path`.
- Blockers: `security`, `run_evidence`, `upgrade_restore`, `golden_path`.

### `secure_production_deployment`

- Target profile: `blessed_core`; target status: `required`.
- Current maturity: `blocked`; readiness: `blocked`.
- Applicable gates: `security`.
- Blockers: `security`.

### `upgrade_restore`

- Target profile: `blessed_core`; target status: `required`.
- Current maturity: `planned`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`.
- Blockers: `security`, `upgrade_restore`, `golden_path`.

## Packages

### `phlo`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `maintenance`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `maintenance`.

### `phlo-airbyte`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-alerting`

- Target profile: `optional`; target status: `supported`.
- Current maturity: `alpha`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-alloy`

- Target profile: `optional`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-api`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.

### `phlo-clickhouse`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-clickstack`

- Target profile: `optional`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-core-plugins`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`.
- Blockers: `security`, `upgrade_restore`, `golden_path`.

### `phlo-dagster`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.

### `phlo-dbt`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `run_evidence`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `run_evidence`.

### `phlo-delta`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-dlt`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `run_evidence`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `run_evidence`.

### `phlo-grafana`

- Target profile: `optional`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-hasura`

- Target profile: `optional`; target status: `supported`.
- Current maturity: `alpha`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-iceberg`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.

### `phlo-kafka`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-lineage`

- Target profile: `optional`; target status: `supported`.
- Current maturity: `alpha`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-loki`

- Target profile: `optional`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-mcp`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-minio`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `maintenance`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `maintenance`.

### `phlo-nessie`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.

### `phlo-oauth2-proxy`

- Target profile: `optional`; target status: `supported`.
- Current maturity: `alpha`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-observatory`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `run_evidence`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `run_evidence`.

### `phlo-observatory-example`

- Target profile: `outside_v1`; target status: `development_only`.
- Current maturity: `development_only`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-observe-plugin`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `run_evidence`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `run_evidence`.

### `phlo-openmetadata`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-otel`

- Target profile: `optional`; target status: `supported`.
- Current maturity: `alpha`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-pandera`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `run_evidence`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `run_evidence`.

### `phlo-pgweb`

- Target profile: `outside_v1`; target status: `development_only`.
- Current maturity: `development_only`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-polaris`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-postgres`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.

### `phlo-postgrest`

- Target profile: `optional`; target status: `supported`.
- Current maturity: `alpha`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-prometheus`

- Target profile: `optional`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-rustfs`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-sling`

- Target profile: `optional`; target status: `supported`.
- Current maturity: `alpha`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-superset`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-testing`

- Target profile: `outside_v1`; target status: `development_only`.
- Current maturity: `development_only`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-traefik`

- Target profile: `optional`; target status: `supported`.
- Current maturity: `alpha`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-transform`

- Target profile: `outside_v1`; target status: `development_only`.
- Current maturity: `development_only`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-trino`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.

## Services

### `airbyte`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `airbyte-bootloader`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `airbyte-manifest`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `airbyte-temporal`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `alloy`

- Target profile: `optional`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `alloy-docker`

- Target profile: `optional`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `clickhouse`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `clickhouse-setup`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `clickstack`

- Target profile: `optional`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `dagster`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.

### `dagster-daemon`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.

### `grafana`

- Target profile: `optional`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `hasura`

- Target profile: `optional`; target status: `supported`.
- Current maturity: `alpha`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `kafka`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `loki`

- Target profile: `optional`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `loki-setup`

- Target profile: `optional`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `minio`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `maintenance`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `maintenance`.

### `minio-setup`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `maintenance`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `maintenance`.

### `minio-volume-setup`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `maintenance`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `maintenance`.

### `nessie`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.

### `oauth2-proxy`

- Target profile: `optional`; target status: `supported`.
- Current maturity: `alpha`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `observatory`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.

### `openmetadata`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `openmetadata-elasticsearch`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `openmetadata-mysql`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `openmetadata-setup`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `pgweb`

- Target profile: `outside_v1`; target status: `development_only`.
- Current maturity: `development_only`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-api`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.

### `phlo-observer`

- Target profile: `optional`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `phlo-observer-db-setup`

- Target profile: `optional`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `polaris`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `polaris-bootstrap`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `postgres`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.

### `postgres-exporter`

- Target profile: `optional`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `postgres-volume-setup`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `maintenance`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `maintenance`.

### `postgrest`

- Target profile: `optional`; target status: `supported`.
- Current maturity: `alpha`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `prometheus`

- Target profile: `optional`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `prometheus-setup`

- Target profile: `optional`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `rustfs`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `rustfs-setup`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `rustfs-volume-setup`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `superset`

- Target profile: `outside_v1`; target status: `preview`.
- Current maturity: `preview`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `traefik`

- Target profile: `optional`; target status: `supported`.
- Current maturity: `alpha`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `traefik-docker`

- Target profile: `optional`; target status: `supported`.
- Current maturity: `alpha`; readiness: `not gated`.
- Applicable gates: none.
- Blockers: none.

### `trino`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.
