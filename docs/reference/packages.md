# Package reference

Package versions and classifications come from the bundled support manifest. Package READMEs contain package-specific implementation details.

The table's **target status** is the intended boundary, not a production-readiness claim. The current Phlo release is alpha and not production-ready; even entries whose target status is `supported` can have blocked release gates. Use the generated [support matrix](support-matrix.md) for current maturity, readiness, gates, and blockers.

## Install extras

| Extra | Packages |
| --- | --- |
| `defaults` | `phlo-core-plugins`, `phlo-api`, `phlo-iceberg`, `phlo-dlt`, `phlo-dbt`, `phlo-pandera`, `phlo-dagster`, `phlo-postgres`, `phlo-minio`, `phlo-nessie`, `phlo-observatory`, `phlo-trino` |

## Package index

| Package | Area | Target status |
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
| `phlo-transform` | SQL transform capability | development-only |
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

The Observatory surface provides an authenticated durable per-run report API and UI projection at alpha maturity. The support registry records this capability under `phlo-observatory` and `phlo-api`. Authentication and route authorisation remain configuration and deployment concerns described in [Auth and access](auth-and-access.md).

## phlo-pandera

Provides Pandera schemas, quality checks, the Pandera provider, and quality Observatory extension support. Install with `pip install phlo-pandera`. It defines no default service. Settings are provider-local.

## phlo-postgres

Provides Postgres resources and service metadata. Install with `pip install phlo-postgres`. Services: `postgres`, `postgres-volume-setup`, and `postgres-exporter`. Settings class: `PostgresSettings`.

## phlo-transform

Provides asset and transformation-provider plugins for declarations created with `phlo.transform.sql`. Install with `pip install phlo-transform`. It defines no service or settings.

## phlo-trino

Provides Trino query integration, shell support, and catalog generation. Install with `pip install phlo-trino`. Service: `trino`. Settings class: `TrinoSettings`.

## phlo-alerting

Provides alert destination integrations and the `alerts` CLI group. Install with `pip install phlo-alerting`. Support tier: supported. Settings class: `AlertingSettings`.

**Enable:** Install the package. **Settings:** Set destination settings under `PHLO_ALERT_*`. Alert destinations receive failure events from Dagster's alerting sensor.

## phlo-hasura

Provides Hasura API integration and the `hasura` CLI group. Install with `pip install phlo-hasura`. Support tier: supported. Settings are package-local.

**Enable:** Run `phlo services add --service hasura`. **Settings:** Set `HASURA_PORT`, default `8082`. Hasura exposes governed data and is not an asset execution provider.

## phlo-lineage

Provides lineage metadata and the `lineage` CLI group. Install with `pip install phlo-lineage`. Support tier: supported. Settings class: `LineageSettings`.

**Enable:** Install the package. **Settings:** Set `PHLO_LINEAGE_DB_URL` or use `DAGSTER_PG_DB_CONNECTION_STRING` for the lineage store. **Runs through Dagster as:** A hook consumer that records lineage from normal asset runs.

## phlo-oauth2-proxy

Provides OAuth2 Proxy service metadata for OIDC front-door access. Install with `pip install phlo-oauth2-proxy`. Support tier: supported.

**Enable:** Add the `proxy` profile after configuring the OIDC settings in its service manifest. **Settings:** OIDC settings are defined by the service manifest. OAuth2 Proxy protects HTTP entry points and is not an asset provider.

## phlo-otel

Provides OpenTelemetry configuration and instrumentation. Install with `pip install phlo-otel`. Support tier: supported.

**Enable:** Install the package. **Settings:** Set standard `OTEL_*` variables such as `OTEL_EXPORTER_OTLP_ENDPOINT` and `OTEL_SERVICE_NAME`. **Runs through Dagster as:** Instrumentation inside Dagster and provider processes.

## phlo-postgrest

Provides PostgREST API integration and service metadata. Install with `pip install phlo-postgrest`. Support tier: supported. Settings class: `PostgrestSettings`.

**Enable:** Run `phlo services add --service postgrest`. **Settings:** Set `POSTGREST_PORT`, default `3002`. PostgREST is an API surface and is not an asset provider.

## phlo-sling

Provides Sling replication decorators, assets, and the `sling` CLI group. Install with `pip install phlo-sling`. Support tier: supported. Settings class: `SlingSettings`.

**Enable:** Install it and declare Sling assets in workflows. **Settings:** Use `SlingSettings` and the package environment settings. **Runs through Dagster as:** Sling assets launched by `phlo materialize` or `phlo backfill`. `phlo sling run` calls Sling directly for inspection or debugging.

## phlo-traefik

Provides Traefik reverse-proxy service metadata. Install with `pip install phlo-traefik`. Support tier: supported.

**Enable:** Add the `proxy` profile. **Settings:** Set `TRAEFIK_HTTP_PORT`, default `80`, and `TRAEFIK_DOMAIN`, default `phlo.localhost`. Traefik routes HTTP services and is not an asset provider.

## phlo-airbyte

Provides Airbyte control-plane integration and the `airbyte` CLI group. Install with `pip install phlo-airbyte`. Support tier: preview. Settings class: `AirbyteSettings`.

**Enable:** Run `phlo services add --service airbyte`. **Settings:** Set `AIRBYTE_PORT`, default `10020`, through `AirbyteSettings`. **Runs through Dagster as:** Airbyte assets declared in workflows. The `phlo airbyte` group calls the control plane directly.

## phlo-alloy

Provides Alloy telemetry collector service metadata. Install with `pip install phlo-alloy`. Support tier: preview.

**Enable:** Add the observability profile. **Settings:** Set `ALLOY_PORT`, default `12345`. Alloy receives and routes telemetry. It is not an asset provider.

## phlo-clickhouse

Provides ClickHouse integration and the `clickhouse` CLI group. Install with `pip install phlo-clickhouse`. Support tier: preview. Settings class: `ClickHouseSettings`. Services: `clickhouse`, `clickhouse-setup`.

**Enable:** Run `phlo services add --service clickhouse`. **Settings:** Set `CLICKHOUSE_HTTP_PORT`, `CLICKHOUSE_NATIVE_PORT`, and `CLICKHOUSE_METRICS_PORT` through `ClickHouseSettings`. **Runs through Dagster as:** ClickHouse assets launched by Dagster. The `phlo clickhouse` group is for direct inspection.

## phlo-clickstack

Provides ClickStack observability service metadata and CLI integration. Install with `pip install phlo-clickstack`. Support tier: preview.

**Enable:** Add the observability profile. **Settings:** Set `CLICKSTACK_PORT`, `CLICKSTACK_HTTP_PORT`, or `CLICKSTACK_NATIVE_PORT` as needed. ClickStack receives observability data and is not an asset provider.

## phlo-delta

Provides Delta Lake integration. Install with `pip install phlo-delta`. Support tier: preview. Settings class: `DeltaSettings`.

**Enable:** Install the package and configure its provider capability. **Settings:** Use `DeltaSettings` values from the package settings module. **Runs through Dagster as:** Delta assets declared by a provider workflow.

## phlo-grafana

Provides Grafana dashboard service metadata. Install with `pip install phlo-grafana`. Support tier: preview. Service: `grafana`.

**Enable:** Add the observability profile. **Settings:** Set `GRAFANA_PORT`, default `3003`. Grafana displays metrics and dashboards and is not an asset provider.

## phlo-kafka

Provides Kafka ingestion, checkpoint lifecycle, and the `kafka` CLI group. Install with `pip install phlo-kafka`. Support tier: preview. Settings class: `KafkaSettings`.

**Enable:** Run `phlo services add --service kafka`. **Settings:** Set `KAFKA_PORT`, default `10021`, through `KafkaSettings`. **Runs through Dagster as:** Kafka ingestion assets launched by Dagster. The `phlo kafka` group forwards provider arguments directly.

## phlo-loki

Provides Loki log-storage service metadata. Install with `pip install phlo-loki`. Support tier: preview. Service: `loki`.

**Enable:** Add the observability profile. **Settings:** Set `LOKI_PORT`, default `3100`. Loki stores logs and is not an asset provider.

## phlo-mcp

Provides the MCP server, tools, prompts, and package documentation resources. Install with `pip install phlo-mcp`. Support tier: preview.

**Enable:** Enable the MCP service through its service definition. **Settings:** Configure the endpoint and client authentication in the service settings. MCP exposes read-only project and runtime resources and does not run assets.

## phlo-observe-plugin

Provides hook translation, Dagster run sensors, and provider instrumentation for Observatory. Install with `pip install phlo-observe-plugin`. Support tier: preview. No packaged README exists for this package.

**Enable:** Add the `phlo-observer` service. **Settings:** Set `PHLO_OBSERVER_PORT`, `PHLO_OBSERVER_DATABASE_URL`, and the ingest, read, and admin token settings. **Runs through Dagster as:** A consumer of Dagster and hook events rather than an asset definition provider.

## phlo-openmetadata

Provides OpenMetadata catalog integration and the `openmetadata` CLI group. Install with `pip install phlo-openmetadata`. Support tier: preview. Settings class: `OpenMetadataSettings`.

**Enable:** Run `phlo services add --service openmetadata`. **Settings:** Set `OPENMETADATA_PORT`, `OPENMETADATA_ADMIN_PORT`, `OPENMETADATA_DB_PORT`, and `OPENMETADATA_ES_PORT` through `OpenMetadataSettings`. **Runs through Dagster as:** A metadata provider consuming Dagster and hook events. The CLI is for direct catalog inspection.

## phlo-polaris

Provides Polaris snapshot-promotion catalog integration. Install with `pip install phlo-polaris`. Support tier: preview. Settings class: `PolarisSettings`.

**Enable:** Run `phlo services add --service polaris`. **Settings:** Set `POLARIS_PORT`, default `10018`, through `PolarisSettings`. **Runs through Dagster as:** The catalog promotion capability used by Dagster WAP runs. Polaris is not a query engine.

## phlo-prometheus

Provides Prometheus metrics service metadata. Install with `pip install phlo-prometheus`. Support tier: preview. Service: `prometheus`.

**Enable:** Add the observability profile. **Settings:** Set `PROMETHEUS_PORT`, default `9090`. Prometheus stores metrics emitted by instrumented runs and is not an asset provider.

## phlo-rustfs

Provides RustFS object-storage integration and service metadata. Install with `pip install phlo-rustfs`. Support tier: preview. Settings class: `RustfsSettings`. Services: `rustfs`, `rustfs-setup`.

**Enable:** Run `phlo services add --service rustfs`. **Settings:** Set `RUSTFS_API_PORT`, default `9000`, and `RUSTFS_CONSOLE_PORT`, default `9001`, through `RustfsSettings`. **Runs through Dagster as:** Object storage used by provider assets.

## phlo-superset

Provides Superset BI service metadata and integration. Install with `pip install phlo-superset`. Support tier: preview. Settings class: `SupersetSettings`.

**Enable:** Run `phlo services add --service superset`. **Settings:** Set `SUPERSET_PORT`, default `8088` in the service manifest. Superset queries published data and is not an asset provider.

## phlo-observatory-example

Provides an Observatory example extension. Install with `pip install phlo-observatory-example`. Support tier: development-only.

## phlo-pgweb

Provides the pgweb development service. Install with `pip install phlo-pgweb`. Support tier: development-only.

**Enable:** Run `phlo services add --service pgweb`. **Settings:** Set `PGWEB_PORT`, default `8081`. pgweb is a development database UI and is not an asset provider.

## phlo-testing

Provides the first-party testing harness used by packages and repository test suites. Install with `pip install phlo-testing`. Support tier: development-only.

**Enable:** Install the package in a development environment. **Settings:** The testing helpers use project test configuration. Its helpers execute tests and do not create production Dagster runs.

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
| `phlo-transform` | SQL transform capability | [README](../../packages/phlo-transform/README.md) |
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
