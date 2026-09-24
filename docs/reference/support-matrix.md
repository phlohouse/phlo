# Support matrix

> Generated from `registry/support/v1.json` by `scripts/generate_reference_docs.py`. Do not edit this page directly.

Release `0.17.0` has `alpha` maturity. Production readiness is `false`.

A target profile states the intended support boundary. Maturity and readiness state what is available now. Gates and blockers state what must pass before release.

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

### `trino`

- Target profile: `blessed_core`; target status: `supported`.
- Current maturity: `alpha`; readiness: `blocked`.
- Applicable gates: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.
- Blockers: `security`, `upgrade_restore`, `golden_path`, `run_evidence`, `maintenance`.
