# Configuration reference

Phlo reads project configuration from `phlo.yaml` and settings from environment variables. Settings models use case-insensitive matching and ignore unknown environment keys.

## Files

| File | Scope | Precedence |
| --- | --- | --- |
| `phlo.yaml` | Project infrastructure and service declarations. | Separate project configuration. |
| `.phlo/.env` | Legacy generated defaults. | Lowest environment-file precedence. |
| `.phlo/.env.local` | Legacy local overrides and secrets. | Higher than `.phlo/.env`. |
| `.phlo/overrides/.env` | Project defaults and local overrides in the shared layout. | Higher than the legacy files. |
| `.phlo/secrets/.env` | Credentials in the shared layout. | Highest environment-file precedence. |
| Process environment | Runtime settings and overrides. | Higher than the project environment files. |

`BaseConfig` uses `env_file=None`, `case_sensitive=False`, and `extra="ignore"`. The project resolves the four environment files in the order shown through `src/phlo/config/layout.py`. New projects write defaults to `.phlo/overrides/.env` and credentials to `.phlo/secrets/.env`; the legacy files remain readable for existing projects.

## phlo.yaml

`phlo.yaml` keeps API policy, WAP policy, service overrides, and generated infrastructure in separate top-level sections. `src/phlo/config_schema.py` defines the models below.

### Top-level sections

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `api` | `ApiConfig \| None` | `None` | API configuration. |
| `wap` | `WapConfig` | `WapConfig()` | Write-Audit-Publish launch policy. |
| `services` | `dict[str, ServiceOverride]` | `{}` | Per-service overrides. |
| `infrastructure` | `InfrastructureConfig` | `InfrastructureConfig()` | Generated container and network configuration. |

### InfrastructureConfig

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `container_backend` | `docker \| podman \| auto` | `docker` | Container backend for service commands. |
| `container_naming_pattern` | `str` | `{project}-{service}-1` | Generated container-name pattern. |
| `services` | `dict[str, ServiceConfig]` | `{}` | Generated service definitions. |
| `network` | `NetworkConfig` | `NetworkConfig()` | Generated network settings. |

### ApiConfig

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `authorization` | `ApiAuthorizationConfig \| None` | `None` | API authorisation settings. |

### ApiAuthorizationConfig

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `backend` | `str \| None` | `None` | Authorisation capability name. |
| `mode` | `str \| None` | `None` | `optional` or `required`. |

For API authorisation, `services.phlo-api.authorization` takes precedence over `api.authorization`.

### WapConfig

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `enabled` | `bool` | `false` | Route materialize and backfill launches through WAP. |
| `strategy` | `Literal["branch", "snapshot"]` | `"branch"` | WAP staging strategy. |
| `job_name` | `str` | `"__ASSET_JOB"` | Dagster asset job. |
| `repository_location_name` | `str \| None` | `"phlo_dagster.framework.definitions"` | Dagster code location. |
| `repository_name` | `str \| None` | `"__repository__"` | Dagster repository. |
| `dagster_url` | `str \| None` | `None` | Remote Dagster GraphQL endpoint. |

### ServiceOverride

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `enabled` | `bool` | `true` | Include the service. |
| `ports` | `list[str] \| None` | `None` | Replace package port mappings. |
| `environment` | `dict[str, str] \| None` | `None` | Merge service environment values. |
| `volumes` | `list[str] \| None` | `None` | Append volume mounts. |
| `extra_hosts` | `list[str] \| None` | `None` | Add Compose host mappings. |
| `depends_on` | `list[str] \| None` | `None` | Replace service dependencies. |
| `command` | `str \| list[str] \| None` | `None` | Replace the container command. |
| `authorization` | `ApiAuthorizationConfig \| None` | `None` | Service-scoped API authorisation. |
| `type` | `str \| None` | `None` | Set to `inline` for a custom service. |
| `image` | `str \| None` | `None` | Image for an inline service. |
| `build` | `dict[str, Any] \| None` | `None` | Build configuration for an inline service. |
| `healthcheck` | `dict[str, Any] \| None` | `None` | Healthcheck for an inline service. |

### NetworkConfig

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | `str \| None` | `None` | Docker network name. |
| `driver` | `str` | `"bridge"` | Docker network driver. |

## Tenant scope

Tenant scope is a request and resource attribute used by API and Observatory surfaces. A tenant identifies the logical boundary for principals, datasets, runs, and policy evaluation. The API authorisation layer receives tenant context with the authenticated principal and applies route guards before provider operations.

Tenant scope does not change the Docker Compose project name, the Iceberg namespace, or the Nessie reference. Those are independent project, catalog, and branch identifiers.

## Environment variables

Environment names are case-insensitive. Core fields use explicit aliases in `src/phlo/config/settings.py`. Package settings use package-specific aliases where defined.

### Core settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `PHLO_ORCHESTRATOR` | `dagster` | Orchestrator adapter. |
| `PHLO_ORCHESTRATOR_NAME` | `dagster` | Alternate orchestrator alias. |
| `PHLO_LOG_LEVEL` | `WARNING` | Default log level. |
| `PHLO_LOG_FORMAT` | `auto` | `auto`, `json`, or `console`. |
| `PHLO_LOG_ROUTER_ENABLED` | `true` | Emit log events to the hook bus. |
| `PHLO_LOG_SERVICE_NAME` | `phlo` | Default log service name. |
| `PHLO_LOG_FILE_TEMPLATE` | `.phlo/logs/{YMD}.log` | Log file template. |
| `PHLO_ENVIRONMENT` | `dev` | Runtime environment label. |
| `ENVIRONMENT` | `dev` | Alternate environment alias. |
| `PHLO_SERVICE_NAMESPACE` | `phlo` | Observability service namespace. |
| `OTEL_SERVICE_NAMESPACE` | `phlo` | Alternate namespace alias. |
| `PHLO_SERVICE_VERSION` | unset | Service version. |
| `OTEL_SERVICE_VERSION` | unset | Alternate service version alias. |
| `PHLO_SERVICE_INSTANCE_ID` | unset | Service instance identifier. |
| `OTEL_SERVICE_INSTANCE_ID` | unset | Alternate instance identifier alias. |
| `PHLO_PROJECT` | unset | Project identifier. |
| `PHLO_DEFAULT_CAPABILITIES` | `{}` | Capability provider mapping. |
| `PLUGINS_ENABLED` | `true` | Enable plugins. |
| `PLUGINS_AUTO_DISCOVER` | `true` | Discover entry points on import. |
| `PLUGINS_WHITELIST` | `[]` | Plugin names allowed to load. |
| `PLUGINS_BLACKLIST` | `[]` | Plugin names excluded from loading. |
| `PLUGIN_REGISTRY_URL` | `https://registry.phlohouse.com/plugins.json` | Plugin registry URL. |
| `PLUGIN_REGISTRY_CACHE_TTL_SECONDS` | `3600` | Registry cache lifetime. |
| `PLUGIN_REGISTRY_TIMEOUT_SECONDS` | `10` | Registry request timeout. |

### Canonical observability settings

The observability profile writes the local observer endpoint and enables concise pretty output. You can override these values in the top-level `env` block in `phlo.yaml`, a project environment file, or the process environment.

| Variable | Profile default | Meaning |
| --- | --- | --- |
| `OBSERVE_HTTP_ENDPOINT` | `http://localhost:10010/v1/events` | Send canonical events to this ingest endpoint. Setting an endpoint enables canonical event emission. |
| `OBSERVE_HTTP_TOKEN` | unset | Bearer token for the observer ingest endpoint. |
| `OBSERVE_HTTP_API_KEY` | unset | API key for the observer ingest endpoint. |
| `OBSERVE_DRAINS` | unset | Comma-separated observe drains. Use `pretty` to select Phlo's formatted console drain explicitly. |
| `PHLO_OBSERVE_ENABLED` | inferred | Set to `false` to disable canonical event emission. Set to `true` to enable SDK defaults without an endpoint. |
| `PHLO_OBSERVE_PRETTY` | `true` | Print canonical events as a concise run narrative. |
| `PHLO_OBSERVE_PRETTY_VERBOSE` | `false` | Include secondary events, diagnostic fields, and the full framework log stream in pretty output. |

`PHLO_LOG_FORMAT` configures the structlog console renderer. It does not enable canonical pretty output. See [Add observability](../guides/add-observability.md) for the setup commands and a `phlo.yaml` example.

### Default-stack settings

#### DagsterSettings

| Variable | Default | Meaning |
| --- | --- | --- |
| `DAGSTER_PORT` | `10006` | Dagster webserver port. |
| `PHLO_WORKFLOWS_PATH` | `workflows` | Workflow directory. |
| `WORKFLOWS_PATH` | `workflows` | Alternate workflow directory alias. |
| `PHLO_FORCE_IN_PROCESS_EXECUTOR` | `false` | Force in-process execution. |
| `PHLO_FORCE_MULTIPROCESS_EXECUTOR` | `false` | Force multiprocess execution. |
| `PHLO_HOST_PLATFORM` | unset | Host platform override. |

#### DltSettings

| Variable | Default | Meaning |
| --- | --- | --- |
| `DLT_DEFAULT_NAMESPACE` | `raw` | Default ingestion namespace. |

#### IcebergSettings

| Variable | Default | Meaning |
| --- | --- | --- |
| `PHLO_ICEBERG_WAREHOUSE_PATH` | `s3://lake/warehouse` | Iceberg warehouse path. |
| `ICEBERG_WAREHOUSE_PATH` | `s3://lake/warehouse` | Alias for the Iceberg warehouse path. |
| `ICEBERG_STAGING_PATH` | `s3://lake/stage` | Staging path. |
| `ICEBERG_DEFAULT_NAMESPACE` | `raw` | Default namespace. |
| `ICEBERG_DEFAULT_REF` | `main` | Default Nessie reference. |
| `PHLO_ICEBERG_S3_ENDPOINT` | `http://minio:10001` | S3 endpoint. |
| `ICEBERG_S3_ENDPOINT` | `http://minio:10001` | Alias for the S3 endpoint. |
| `PHLO_ICEBERG_S3_ACCESS_KEY` | `minio` | S3 access key. |
| `ICEBERG_S3_ACCESS_KEY` | `minio` | Alias for the S3 access key. |
| `PHLO_ICEBERG_S3_SECRET_KEY` | `minio123` | S3 secret key. |
| `ICEBERG_S3_SECRET_KEY` | `minio123` | Alias for the S3 secret key. |
| `PHLO_ICEBERG_S3_REGION` | `us-east-1` | S3 region. |
| `ICEBERG_S3_REGION` | `us-east-1` | Alias for the S3 region. |
| `PHLO_ICEBERG_CATALOG_URI` | `http://nessie:19120/iceberg` | Iceberg REST catalog URI. |
| `ICEBERG_CATALOG_URI` | `http://nessie:19120/iceberg` | Alias for the Iceberg REST catalog URI. |
| `AWS_ACCESS_KEY_ID` | `minio` | Alternate S3 access key. |
| `AWS_SECRET_ACCESS_KEY` | `minio123` | Alternate S3 secret key. |
| `AWS_REGION` | `us-east-1` | Alternate S3 region. |
| `AWS_DEFAULT_REGION` | `us-east-1` | Alternate S3 region alias. |

#### MinioSettings

| Variable | Default | Meaning |
| --- | --- | --- |
| `MINIO_HOST` | `minio` | MinIO hostname. |
| `MINIO_ROOT_USER` | `minio` | MinIO root username. |
| `MINIO_ROOT_PASSWORD` | `minio123` | MinIO root password. |
| `MINIO_API_PORT` | `10001` | MinIO API port. |
| `MINIO_CONSOLE_PORT` | `10002` | MinIO console port. |
| `S3_REGION` | `us-east-1` | S3 region. |

#### NessieSettings

| Variable | Default | Meaning |
| --- | --- | --- |
| `NESSIE_VERSION` | `0.108.3` | Nessie image version. |
| `NESSIE_PORT` | `19120` | Nessie REST API port. |
| `NESSIE_HOST` | `nessie` | Nessie hostname. |
| `NESSIE_API_VERSION` | `v1` | Nessie API version. |
| `NESSIE_DEFAULT_REF` | `main` | Default branch or tag. |
| `NESSIE_QUERY_ENGINE` | unset | Optional query engine capability. |

#### TrinoSettings

| Variable | Default | Meaning |
| --- | --- | --- |
| `TRINO_VERSION` | `477` | Trino image version. |
| `TRINO_PORT` | `10005` | Trino HTTP port. |
| `TRINO_HOST` | `trino` | Trino hostname. |
| `TRINO_CATALOG` | `iceberg` | Default catalog. |
| `TRINO_DEFAULT_REF` | `main` | Default branch or tag suffix. |

#### PostgresSettings

| Variable | Default | Meaning |
| --- | --- | --- |
| `POSTGRES_HOST` | `postgres` | PostgreSQL hostname. |
| `POSTGRES_PORT` | `5432` | PostgreSQL port. |
| `POSTGRES_USER` | `phlo` | PostgreSQL username. |
| `POSTGRES_PASSWORD` | `phlo` | PostgreSQL password. |
| `POSTGRES_DB` | `phlo` | PostgreSQL database. |
| `POSTGRES_MART_SCHEMA` | `marts` | Published table schema. |

#### ObservatorySettings

| Variable | Default | Meaning |
| --- | --- | --- |
| `PHLO_OBSERVATORY_SETTINGS_DB_URL` | unset | Observatory settings database DSN. |

#### Pandera settings

`phlo-pandera` has no settings class. Its decorator defaults are documented in [the Python API reference](python-api.md).

### Other package settings

The remaining package settings classes are `AirbyteSettings`, `AlertingSettings`, `ClickHouseSettings`, `DbtSettings`, `DeltaSettings`, `KafkaSettings`, `LineageSettings`, `OpenMetadataSettings`, `PolarisSettings`, `RustfsSettings`, `SlingSettings`, and `SupersetSettings`. Their fields and aliases are defined in the corresponding `packages/<package>/src/<module>/settings.py` file.
