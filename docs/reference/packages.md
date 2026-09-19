# Package reference

Package versions and support classifications come from `registry/support/v1.json`. Package READMEs contain package-specific implementation details.

## Install extras

| Extra | Packages |
| --- | --- |
| `defaults` | `phlo-core-plugins`, `phlo-api`, `phlo-iceberg`, `phlo-dlt`, `phlo-dbt`, `phlo-pandera`, `phlo-dagster`, `phlo-postgres`, `phlo-minio`, `phlo-nessie`, `phlo-observatory`, `phlo-trino` |

## Package index

| Package | Area | Support tier |
| --- | --- | --- |
| `phlo` | Core CLI and runtime | supported |
| `phlo-api` | API and Observatory integration | supported |
| `phlo-core-plugins` | Core plugin implementations | supported |
| `phlo-dagster` | Dagster orchestration | supported |
| `phlo-dbt` | dbt transformations | supported |
| `phlo-dlt` | DLT ingestion | supported |
| `phlo-iceberg` | Iceberg table storage | supported |
| `phlo-minio` | S3-compatible object storage | supported |
| `phlo-nessie` | Nessie catalog | supported |
| `phlo-observatory` | Run and platform UI | supported |
| `phlo-pandera` | Data quality | supported |
| `phlo-postgres` | Postgres service | supported |
| `phlo-trino` | Trino query engine | supported |
| `phlo-alerting` | Alert destinations | supported |
| `phlo-hasura` | Hasura API | supported |
| `phlo-lineage` | Lineage | supported |
| `phlo-oauth2-proxy` | OIDC proxy | supported |
| `phlo-otel` | OpenTelemetry | supported |
| `phlo-postgrest` | PostgREST API | supported |
| `phlo-sling` | Sling replication | supported |
| `phlo-traefik` | Reverse proxy | supported |
| `phlo-airbyte` | Airbyte integration | preview |
| `phlo-alloy` | Telemetry collector | preview |
| `phlo-clickhouse` | ClickHouse storage | preview |
| `phlo-clickstack` | ClickHouse observability stack | preview |
| `phlo-delta` | Delta Lake storage | preview |
| `phlo-grafana` | Metrics dashboards | preview |
| `phlo-kafka` | Kafka ingestion | preview |
| `phlo-loki` | Log storage | preview |
| `phlo-mcp` | MCP server | preview |
| `phlo-observe-plugin` | Observatory event translation | preview |
| `phlo-openmetadata` | Metadata catalog | preview |
| `phlo-polaris` | Polaris catalog | preview |
| `phlo-prometheus` | Metrics storage | preview |
| `phlo-rustfs` | S3-compatible object storage | preview |
| `phlo-superset` | BI interface | preview |
| `phlo-observatory-example` | Observatory example | development-only |
| `phlo-pgweb` | Postgres development UI | development-only |
| `phlo-testing` | First-party test harness | development-only |

## phlo

The core package provides the CLI, configuration, capability interfaces, plugin discovery, flow declarations, and public Python API. Install with `pip install phlo`. It defines no container service.

## phlo-api

Provides the API backend and Observatory API routes. Install with `pip install phlo-api`. It defines the `phlo-api` service. Settings class: `ApiSettings`.

## phlo-core-plugins

Provides core source, quality, and service plugin implementations. Install with `pip install phlo-core-plugins`. Settings are inherited from core providers.

## phlo-dagster

Provides Dagster asset discovery, execution, and the Dagster webserver and daemon services. Install with `pip install phlo-dagster`. Settings class: `DagsterSettings`.

## phlo-dbt

Provides dbt asset discovery and dbt CLI integration. Install with `pip install phlo-dbt`. Settings class: `DbtSettings`.

## phlo-dlt

Provides DLT ingestion assets, `phlo_ingestion`, and DLT runtime integration. Install with `pip install phlo-dlt`. Settings class: `DltSettings`.

## phlo-iceberg

Provides Iceberg table-store integration and PyIceberg configuration. Install with `pip install phlo-iceberg`. Settings class: `IcebergSettings`.

## phlo-minio

Provides MinIO service metadata and object-storage integration. Install with `pip install phlo-minio`. Settings class: `MinioSettings`. Services: `minio`, `minio-setup`.

## phlo-nessie

Provides Nessie catalog integration and catalog CLI commands. Install with `pip install phlo-nessie`. Settings class: `NessieSettings`. Service: `nessie`.

## phlo-observatory

Provides Observatory UI integration, extension loading, run views, and service metadata. Install with `pip install phlo-observatory`. Settings class: `ObservatorySettings`. Service: `observatory`.

## Durable run-report support boundary

The Observatory surface provides an authenticated durable per-run report API and UI projection at alpha maturity. The support registry records this capability under `phlo-observatory` and `phlo-api`. Authentication and route authorization remain configuration and deployment concerns described in [Auth and access](auth-and-access.md).

## phlo-pandera

Provides Pandera schemas, quality checks, the Pandera provider, and quality Observatory extension support. Install with `pip install phlo-pandera`. It defines no default service. Settings are provider-local.

## phlo-postgres

Provides Postgres resources and service metadata. Install with `pip install phlo-postgres`. Services: `postgres`, `postgres-volume-setup`, and `postgres-exporter`. Settings class: `PostgresSettings`.

## phlo-trino

Provides Trino query integration, shell support, and catalog generation. Install with `pip install phlo-trino`. Service: `trino`. Settings class: `TrinoSettings`.

## phlo-alerting

Provides alert destination integrations and the `alerts` CLI group. Install with `pip install phlo-alerting`. Support tier: supported. Settings class: `AlertingSettings`.

## phlo-hasura

Provides Hasura API integration and the `hasura` CLI group. Install with `pip install phlo-hasura`. Support tier: supported. Settings are package-local.

## phlo-lineage

Provides lineage metadata and the `lineage` CLI group. Install with `pip install phlo-lineage`. Support tier: supported. Settings class: `LineageSettings`.

## phlo-oauth2-proxy

Provides OAuth2 Proxy service metadata for OIDC front-door access. Install with `pip install phlo-oauth2-proxy`. Support tier: supported.

## phlo-otel

Provides OpenTelemetry configuration and instrumentation. Install with `pip install phlo-otel`. Support tier: supported.

## phlo-postgrest

Provides PostgREST API integration and service metadata. Install with `pip install phlo-postgrest`. Support tier: supported. Settings class: `PostgrestSettings`.

## phlo-sling

Provides Sling replication decorators, assets, and the `sling` CLI group. Install with `pip install phlo-sling`. Support tier: supported. Settings class: `SlingSettings`.

## phlo-traefik

Provides Traefik reverse-proxy service metadata. Install with `pip install phlo-traefik`. Support tier: supported.

## phlo-airbyte

Provides Airbyte control-plane integration and the `airbyte` CLI group. Install with `pip install phlo-airbyte`. Support tier: preview. Settings class: `AirbyteSettings`.

## phlo-alloy

Provides Alloy telemetry collector service metadata. Install with `pip install phlo-alloy`. Support tier: preview.

## phlo-clickhouse

Provides ClickHouse integration and the `clickhouse` CLI group. Install with `pip install phlo-clickhouse`. Support tier: preview. Settings class: `ClickHouseSettings`. Services: `clickhouse`, `clickhouse-setup`.

## phlo-clickstack

Provides ClickStack observability service metadata and CLI integration. Install with `pip install phlo-clickstack`. Support tier: preview.

## phlo-delta

Provides Delta Lake integration. Install with `pip install phlo-delta`. Support tier: preview. Settings class: `DeltaSettings`.

## phlo-grafana

Provides Grafana dashboard service metadata. Install with `pip install phlo-grafana`. Support tier: preview. Service: `grafana`.

## phlo-kafka

Provides Kafka ingestion, checkpoint lifecycle, and the `kafka` CLI group. Install with `pip install phlo-kafka`. Support tier: preview. Settings class: `KafkaSettings`.

## phlo-loki

Provides Loki log-storage service metadata. Install with `pip install phlo-loki`. Support tier: preview. Service: `loki`.

## phlo-mcp

Provides the MCP server, tools, prompts, and package documentation resources. Install with `pip install phlo-mcp`. Support tier: preview.

## phlo-observe-plugin

Provides hook translation, Dagster run sensors, and provider instrumentation for Observatory. Install with `pip install phlo-observe-plugin`. Support tier: preview. No packaged README exists for this package.

## phlo-openmetadata

Provides OpenMetadata catalog integration and the `openmetadata` CLI group. Install with `pip install phlo-openmetadata`. Support tier: preview. Settings class: `OpenMetadataSettings`.

## phlo-polaris

Provides Polaris snapshot-promotion catalog integration. Install with `pip install phlo-polaris`. Support tier: preview. Settings class: `PolarisSettings`.

## phlo-prometheus

Provides Prometheus metrics service metadata. Install with `pip install phlo-prometheus`. Support tier: preview. Service: `prometheus`.

## phlo-rustfs

Provides RustFS object-storage integration and service metadata. Install with `pip install phlo-rustfs`. Support tier: preview. Settings class: `RustfsSettings`. Services: `rustfs`, `rustfs-setup`.

## phlo-superset

Provides Superset BI service metadata and integration. Install with `pip install phlo-superset`. Support tier: preview. Settings class: `SupersetSettings`.

## phlo-observatory-example

Provides an Observatory example extension. Install with `pip install phlo-observatory-example`. Support tier: development-only.

## phlo-pgweb

Provides the pgweb development service. Install with `pip install phlo-pgweb`. Support tier: development-only.

## phlo-testing

Provides the first-party testing harness used by packages and repository test suites. Install with `pip install phlo-testing`. Support tier: development-only.

## Package contributions and READMEs

| Package | Contributions | README |
| --- | --- | --- |
| `phlo` | CLI, configuration, APIs | [README](../../README.md) |
| `phlo-api` | API routes and service | [README](../../packages/phlo-api/README.md) |
| `phlo-core-plugins` | Core capabilities | [README](../../packages/phlo-core-plugins/README.md) |
| `phlo-dagster` | Orchestrator, assets, services | [README](../../packages/phlo-dagster/README.md) |
| `phlo-dbt` | dbt assets and CLI | [README](../../packages/phlo-dbt/README.md) |
| `phlo-dlt` | DLT assets and ingestion | [README](../../packages/phlo-dlt/README.md) |
| `phlo-iceberg` | Catalog and table storage | [README](../../packages/phlo-iceberg/README.md) |
| `phlo-minio` | Object storage and services | [README](../../packages/phlo-minio/README.md) |
| `phlo-nessie` | Catalog and CLI | [README](../../packages/phlo-nessie/README.md) |
| `phlo-observatory` | UI, routes, and service | [README](../../packages/phlo-observatory/README.md) |
| `phlo-pandera` | Quality checks and assets | [README](../../packages/phlo-pandera/README.md) |
| `phlo-postgres` | Resources and service | [README](../../packages/phlo-postgres/README.md) |
| `phlo-trino` | Query engine and CLI | [README](../../packages/phlo-trino/README.md) |
| `phlo-alerting` | Alerts CLI and destinations | [README](../../packages/phlo-alerting/README.md) |
| `phlo-hasura` | Hasura CLI and service | [README](../../packages/phlo-hasura/README.md) |
| `phlo-lineage` | Lineage CLI and metadata | [README](../../packages/phlo-lineage/README.md) |
| `phlo-oauth2-proxy` | OAuth2 Proxy service | [README](../../packages/phlo-oauth2-proxy/README.md) |
| `phlo-otel` | Telemetry instrumentation | [README](../../packages/phlo-otel/README.md) |
| `phlo-postgrest` | PostgREST API and service | [README](../../packages/phlo-postgrest/README.md) |
| `phlo-sling` | Replication assets and CLI | [README](../../packages/phlo-sling/README.md) |
| `phlo-traefik` | Reverse-proxy service | [README](../../packages/phlo-traefik/README.md) |
| `phlo-airbyte` | Airbyte CLI and integration | [README](../../packages/phlo-airbyte/README.md) |
| `phlo-alloy` | Telemetry service | [README](../../packages/phlo-alloy/README.md) |
| `phlo-clickhouse` | ClickHouse CLI and service | [README](../../packages/phlo-clickhouse/README.md) |
| `phlo-clickstack` | ClickStack CLI and service | [README](../../packages/phlo-clickstack/README.md) |
| `phlo-delta` | Delta storage | [README](../../packages/phlo-delta/README.md) |
| `phlo-grafana` | Grafana service and UI | [README](../../packages/phlo-grafana/README.md) |
| `phlo-kafka` | Kafka CLI and ingestion | [README](../../packages/phlo-kafka/README.md) |
| `phlo-loki` | Loki service | [README](../../packages/phlo-loki/README.md) |
| `phlo-mcp` | MCP tools and resources | [README](../../packages/phlo-mcp/README.md) |
| `phlo-observe-plugin` | Observatory hooks | No README |
| `phlo-openmetadata` | Metadata CLI and integration | [README](../../packages/phlo-openmetadata/README.md) |
| `phlo-polaris` | Catalog integration | [README](../../packages/phlo-polaris/README.md) |
| `phlo-prometheus` | Prometheus service | [README](../../packages/phlo-prometheus/README.md) |
| `phlo-rustfs` | Object storage and service | [README](../../packages/phlo-rustfs/README.md) |
| `phlo-superset` | BI service and UI | [README](../../packages/phlo-superset/README.md) |
| `phlo-observatory-example` | Observatory example extension | [README](../../packages/phlo-observatory-example/README.md) |
| `phlo-pgweb` | Postgres development UI | [README](../../packages/phlo-pgweb/README.md) |
| `phlo-testing` | Test harness | [README](../../packages/phlo-testing/README.md) |
