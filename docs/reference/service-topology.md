# Service topology reference

> Generated from package service manifests by `scripts/generate_reference_docs.py`. Do not edit directly.

`ServiceDefinition` is the typed authority. These are declared startup dependencies and published port defaults, not runtime calls or deployment overrides. See [ownership and validation](../architecture/service-topology.md#typed-authority-and-projections).

| Service | Package manifest | Dependencies | Host default → container/protocol (override) | Profile | Default |
| --- | --- | --- | --- | --- | --- |
| `airbyte` | [phlo-airbyte](../../packages/phlo-airbyte/src/phlo_airbyte/service.yaml) | `airbyte-manifest`, `airbyte-temporal`, `postgres` | 10020 → 8001/tcp (AIRBYTE_PORT) | none | false |
| `airbyte-bootloader` | [phlo-airbyte](../../packages/phlo-airbyte/src/phlo_airbyte/airbyte-bootloader-setup.yaml) | `postgres` | none | none | false |
| `airbyte-manifest` | [phlo-airbyte](../../packages/phlo-airbyte/src/phlo_airbyte/airbyte-manifest-setup.yaml) | none | 8007 → 8080/tcp (AIRBYTE_MANIFEST_PORT) | none | false |
| `airbyte-temporal` | [phlo-airbyte](../../packages/phlo-airbyte/src/phlo_airbyte/airbyte-temporal-setup.yaml) | `postgres` | 7233 → 7233/tcp (AIRBYTE_TEMPORAL_PORT) | none | false |
| `alloy` | [phlo-alloy](../../packages/phlo-alloy/src/phlo_alloy/service.yaml) | `loki` | 12345 → 12345/tcp (ALLOY_PORT) | observability | false |
| `clickhouse` | [phlo-clickhouse](../../packages/phlo-clickhouse/src/phlo_clickhouse/service.yaml) | none | 8123 → 8123/tcp (CLICKHOUSE_HTTP_PORT); 19000 → 9000/tcp (CLICKHOUSE_NATIVE_PORT); 9363 → 9363/tcp (CLICKHOUSE_METRICS_PORT) | none | false |
| `clickhouse-setup` | [phlo-clickhouse](../../packages/phlo-clickhouse/src/phlo_clickhouse/clickhouse-setup.yaml) | `clickhouse` | none | none | false |
| `clickstack` | [phlo-clickstack](../../packages/phlo-clickstack/src/phlo_clickstack/service.yaml) | none | 18080 → 8080/tcp (CLICKSTACK_PORT); 18123 → 8123/tcp (CLICKSTACK_HTTP_PORT); 19002 → 9000/tcp (CLICKSTACK_NATIVE_PORT) | observability | false |
| `dagster` | [phlo-dagster](../../packages/phlo-dagster/src/phlo_dagster/service.yaml) | `minio`, `nessie`, `postgres`, `trino` | 10006 → 3000/tcp (DAGSTER_PORT) | none | true |
| `dagster-daemon` | [phlo-dagster](../../packages/phlo-dagster/src/phlo_dagster/dagster-daemon.yaml) | `dagster` | none | none | true |
| `grafana` | [phlo-grafana](../../packages/phlo-grafana/src/phlo_grafana/service.yaml) | `loki`, `prometheus` | 3003 → 3000/tcp (GRAFANA_PORT) | observability | false |
| `hasura` | [phlo-hasura](../../packages/phlo-hasura/src/phlo_hasura/service.yaml) | `postgres` | 8082 → 8080/tcp (HASURA_PORT) | api | false |
| `kafka` | [phlo-kafka](../../packages/phlo-kafka/src/phlo_kafka/service.yaml) | none | 10021 → 9094/tcp (KAFKA_PORT) | none | false |
| `loki` | [phlo-loki](../../packages/phlo-loki/src/phlo_loki/service.yaml) | none | 3100 → 3100/tcp (LOKI_PORT) | observability | false |
| `minio` | [phlo-minio](../../packages/phlo-minio/src/phlo_minio/service.yaml) | `minio-volume-setup` | 10001 → 9000/tcp (MINIO_API_PORT); 10002 → 9001/tcp (MINIO_CONSOLE_PORT) | none | true |
| `minio-setup` | [phlo-minio](../../packages/phlo-minio/src/phlo_minio/minio-setup.yaml) | `minio` | none | none | true |
| `minio-volume-setup` | [phlo-minio](../../packages/phlo-minio/src/phlo_minio/minio-volume-setup.yaml) | none | none | none | false |
| `nessie` | [phlo-nessie](../../packages/phlo-nessie/src/phlo_nessie/service.yaml) | `minio`, `postgres` | 10003 → 19120/tcp (NESSIE_PORT) | none | true |
| `oauth2-proxy` | [phlo-oauth2-proxy](../../packages/phlo-oauth2-proxy/src/phlo_oauth2_proxy/service.yaml) | none | none | proxy | false |
| `observatory` | [phlo-observatory](../../packages/phlo-observatory/src/phlo_observatory/service.yaml) | `phlo-api` | 3001 → 3000/tcp (OBSERVATORY_PORT) | api | false |
| `openmetadata` | [phlo-openmetadata](../../packages/phlo-openmetadata/src/phlo_openmetadata/service.yaml) | `openmetadata-elasticsearch`, `openmetadata-mysql`, `openmetadata-setup` | 8585 → 8585/tcp (OPENMETADATA_PORT) | openmetadata | false |
| `openmetadata-elasticsearch` | [phlo-openmetadata](../../packages/phlo-openmetadata/src/phlo_openmetadata/openmetadata-elasticsearch-setup.yaml) | none | none | openmetadata | false |
| `openmetadata-mysql` | [phlo-openmetadata](../../packages/phlo-openmetadata/src/phlo_openmetadata/openmetadata-mysql-setup.yaml) | none | none | openmetadata | false |
| `openmetadata-setup` | [phlo-openmetadata](../../packages/phlo-openmetadata/src/phlo_openmetadata/openmetadata-setup.yaml) | `openmetadata-elasticsearch`, `openmetadata-mysql` | none | openmetadata | false |
| `pgweb` | [phlo-pgweb](../../packages/phlo-pgweb/src/phlo_pgweb/service.yaml) | `postgres` | 8081 → 8081/tcp (PGWEB_PORT) | none | true |
| `phlo-api` | [phlo-api](../../packages/phlo-api/src/phlo_api/service.yaml) | `postgres` | 4000 → 4000/tcp (PHLO_API_PORT) | api | false |
| `phlo-observer` | [phlo-observe-plugin](../../packages/phlo-observe-plugin/src/phlo_observe_plugin/service.yaml) | `phlo-observer-db-setup`, `postgres` | 10010 → 8080/tcp (PHLO_OBSERVER_PORT) | observability | false |
| `phlo-observer-db-setup` | [phlo-observe-plugin](../../packages/phlo-observe-plugin/src/phlo_observe_plugin/db-setup.yaml) | `postgres` | none | observability | false |
| `polaris` | [phlo-polaris](../../packages/phlo-polaris/src/phlo_polaris/service.yaml) | `minio`, `postgres` | 10018 → 8181/tcp (POLARIS_PORT) | none | false |
| `postgres` | [phlo-postgres](../../packages/phlo-postgres/src/phlo_postgres/service.yaml) | `postgres-volume-setup` | 10000 → 5432/tcp (POSTGRES_PORT) | none | true |
| `postgres-exporter` | [phlo-postgres](../../packages/phlo-postgres/src/phlo_postgres/exporter_service.yaml) | `postgres` | 9187 → 9187/tcp (POSTGRES_EXPORTER_PORT) | observability | false |
| `postgres-volume-setup` | [phlo-postgres](../../packages/phlo-postgres/src/phlo_postgres/volume_setup.yaml) | none | none | none | false |
| `postgrest` | [phlo-postgrest](../../packages/phlo-postgrest/src/phlo_postgrest/service.yaml) | `postgres` | 3002 → 3000/tcp (POSTGREST_PORT) | api | false |
| `prometheus` | [phlo-prometheus](../../packages/phlo-prometheus/src/phlo_prometheus/service.yaml) | none | 9090 → 9090/tcp (PROMETHEUS_PORT) | observability | false |
| `rustfs` | [phlo-rustfs](../../packages/phlo-rustfs/src/phlo_rustfs/service.yaml) | `rustfs-volume-setup` | 9000 → 9000/tcp (RUSTFS_API_PORT); 9001 → 9001/tcp (RUSTFS_CONSOLE_PORT) | none | false |
| `rustfs-setup` | [phlo-rustfs](../../packages/phlo-rustfs/src/phlo_rustfs/rustfs-setup.yaml) | `rustfs` | none | none | false |
| `rustfs-volume-setup` | [phlo-rustfs](../../packages/phlo-rustfs/src/phlo_rustfs/rustfs-volume-setup.yaml) | none | none | none | false |
| `superset` | [phlo-superset](../../packages/phlo-superset/src/phlo_superset/service.yaml) | `postgres`, `trino` | 8088 → 8088/tcp (SUPERSET_PORT) | none | true |
| `traefik` | [phlo-traefik](../../packages/phlo-traefik/src/phlo_traefik/service.yaml) | none | 80 → 80/tcp (TRAEFIK_HTTP_PORT) | proxy | false |
| `trino` | [phlo-trino](../../packages/phlo-trino/src/phlo_trino/service.yaml) | `minio`, `nessie` | 10005 → 8080/tcp (TRINO_PORT) | none | true |
