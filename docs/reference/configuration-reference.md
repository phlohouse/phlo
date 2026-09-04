# Configuration Reference

Complete reference for configuring Phlo.

## Configuration System

Phlo uses multiple configuration sources:

```mermaid
flowchart TB
    defaults["Infrastructure defaults<br/>phlo.yaml"]
    local["Local secrets and overrides<br/>.phlo/.env.local"]
    runtime[Runtime environment variables]
    python[Python package settings]
    runconfig[Dagster run config]

    defaults --> local --> runtime --> python --> runconfig
```

1. **Infrastructure defaults** (`phlo.yaml`, `env:`)
2. **Local secrets/overrides** (`.phlo/.env.local`)
3. **Runtime environment** (process environment variables)
4. **Python settings** (package settings modules like `phlo_postgres.settings`)
5. **Runtime configuration** (Dagster run config)

## Security Mode

Regulated mode can be enabled either in `phlo.yaml` or with an environment variable.

```yaml
regulated: true

authentication:
  provider: proxy
  proxy:
    trusted_proxies:
      - 127.0.0.1/32
    shared_secret: change-me
    header_subject: X-Remote-User
    header_email: X-Remote-Email
    header_groups: X-Remote-Groups
```

```bash
PHLO_REGULATED=true
```

Precedence:

- `PHLO_REGULATED`
- `phlo.yaml` root `regulated`
- default `false`

Note: `PHLO_REGULATED_MODE` and `regulated_mode:` are accepted as deprecated aliases.

## Tenant scope

The v1 support boundary covers one project and one tenant per deployment. Multi-tenancy configuration and isolation are not supported by this boundary.

Built-in authentication provider names:

- `static`
- `proxy`
- `service_token`

Built-in provider config blocks:

- `authentication.static`
  - `enabled`
  - `dev_mode`
  - `users`
- `authentication.proxy`
  - `enabled`
  - `trusted_proxies`
  - `shared_secret`
  - `header_subject`
  - `header_email`
  - `header_groups`
- `authentication.service_token`
  - `enabled`
  - `tokens`

### Authentication Gateway

The `oauth2-proxy` service provides forward-auth with Traefik for protected services.

```bash
# OAuth2 provider type (default: oidc)
OAUTH2_PROXY_PROVIDER=oidc

# OIDC issuer URL (required for oidc provider)
OAUTH2_PROXY_OIDC_ISSUER_URL=https://your-idp.example.com

# OAuth2 client credentials (from your IdP)
OAUTH2_PROXY_CLIENT_ID=your-client-id
OAUTH2_PROXY_CLIENT_SECRET=your-client-secret

# Cookie encryption secret (generate with: python -c "import secrets; print(secrets.token_urlsafe(32))")
OAUTH2_PROXY_COOKIE_SECRET=<32-byte-base64-string>

# Set to true in production with HTTPS
OAUTH2_PROXY_COOKIE_SECURE=false

# OAuth2 callback URL (must match IdP registration)
OAUTH2_PROXY_REDIRECT_URL=http://api.phlo.localhost/oauth2/callback

# Allowed email domains (default: * for any)
OAUTH2_PROXY_EMAIL_DOMAINS=*
```

## Environment Variables

Environment variables are materialized into `.phlo/.env` (generated, non-secret defaults)
and `.phlo/.env.local` (local secrets). Edit `phlo.yaml` for committed defaults and
`.phlo/.env.local` for secrets.

### Service Credentials (Regulated Mode)

For regulated deployments, use scoped credentials per service instead of shared
admin credentials. See [Service Credentials Guide](../setup/service-credentials.md).

```bash
# Dagster scoped credentials
DAGSTER_POSTGRES_USER=phlo_dagster_service
DAGSTER_POSTGRES_PASSWORD=<secret>
DAGSTER_TRINO_USER=phlo-dagster
DAGSTER_TRINO_ROLE=phlo_dagster_writer
DAGSTER_MINIO_ACCESS_KEY=phlo-dagster-svc
DAGSTER_MINIO_SECRET_KEY=<secret>

# phlo-api scoped credentials
PHLO_API_TRINO_USER=phlo-api
PHLO_API_TRINO_ROLE=phlo_api_reader

# Service-to-service authentication
PHLO_SERVICE_SECRET=<shared-hmac-secret>
```

All scoped vars fall back to the shared defaults when unset.

### Orchestrator Configuration

```bash
# Active orchestrator adapter (default: dagster)
PHLO_ORCHESTRATOR=dagster
# Alias
PHLO_ORCHESTRATOR_NAME=dagster
```

### Logging Configuration

```bash
# Log level (default: WARNING)
PHLO_LOG_LEVEL=WARNING

# Terminal log format: auto keeps stderr quiet; console/json opt into log stderr
PHLO_LOG_FORMAT=auto

# Emit structured log events to the hook bus (default: true)
PHLO_LOG_ROUTER_ENABLED=true

# Default service name attached to log records (default: phlo)
PHLO_LOG_SERVICE_NAME=phlo

# Log file path template with date placeholders (default: .phlo/logs/{YMD}.log)
# Available placeholders: {YMD}, {YM}, {Y}, {YYYY}, {M}, {MM}, {D}, {DD}, {H}, {HM}, {HMS}, {DATE}, {TIMESTAMP}
# Set empty to disable file logging
PHLO_LOG_FILE_TEMPLATE=.phlo/logs/{YMD}.log

# With auto, user-facing CLI messages stay separate from internal diagnostics.
# Structured JSON remains available via the log file, hook bus, or PHLO_LOG_FORMAT=json.

# Default service namespace attached to observability resources (default: phlo)
PHLO_SERVICE_NAMESPACE=phlo

# Optional default service version attached to observability resources
PHLO_SERVICE_VERSION=

# Optional default service instance ID attached to observability resources
PHLO_SERVICE_INSTANCE_ID=

# Optional project identifier attached to observability resources
PHLO_PROJECT=

# Runtime environment attached to logs and observability resources (default: dev)
PHLO_ENVIRONMENT=dev
```

Notes:

- `PHLO_LOG_SERVICE_NAME` is the default `service.name` for `phlo-otel` when `OTEL_SERVICE_NAME` is unset.
- `PHLO_SERVICE_NAMESPACE`, `PHLO_SERVICE_VERSION`, `PHLO_SERVICE_INSTANCE_ID`, and `PHLO_PROJECT` provide Phlo-native defaults for OTel resource metadata.
- Standard `OTEL_*` variables still take precedence when set.

### Capability Selection

```yaml
# phlo.yaml
capabilities:
  defaults:
    table_store: iceberg
    query_engine: trino
```

```bash
# Optional environment override for the same mapping.
PHLO_DEFAULT_CAPABILITIES='{"table_store":"iceberg","query_engine":"trino"}'
```

Capability resolution order:

```mermaid
flowchart TB
    explicit[Explicit provider passed by caller]
    tag["Runtime tag<br/>phlo/capability/<capability_type>=<provider>"]
    asset[Asset capability_overrides]
    env[PHLO_DEFAULT_CAPABILITIES]
    yaml["phlo.yaml capabilities.defaults"]
    implicit[Implicit single installed provider]
    fail[Fail with installed provider names]

    explicit --> tag --> asset --> env --> yaml --> implicit --> fail
```

- explicit provider name passed by the caller
- runtime/workflow tag: `phlo/capability/<capability_type>=<provider>`
- asset-level `capability_overrides`
- `PHLO_DEFAULT_CAPABILITIES`
- `phlo.yaml` `capabilities.defaults`
- implicit selection only when exactly one provider of that capability type is installed

If multiple providers are installed and none of the rules above selects one, Phlo fails with the
installed provider names instead of picking one implicitly.

### Database Configuration

PostgreSQL database settings:

```bash
# Host and port
POSTGRES_HOST=localhost
POSTGRES_PORT=10000

# Credentials
POSTGRES_USER=lake
POSTGRES_PASSWORD=phlo

# Database
POSTGRES_DB=lakehouse
POSTGRES_MART_SCHEMA=marts

# Lineage tracking database (optional, defaults to Dagster Postgres connection)
PHLO_LINEAGE_DB_URL=postgresql://lake:phlo@localhost:10000/lakehouse
# Alternative: DAGSTER_PG_DB_CONNECTION_STRING (alias for lineage_db_url)
```

**Connection string format**:

```
postgresql://lake:phlo@localhost:10000/lakehouse
```

### Storage Configuration

MinIO S3-compatible object storage:

```bash
# Host and ports
MINIO_HOST=localhost
MINIO_API_PORT=10001
MINIO_CONSOLE_PORT=10002

# Credentials
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin
```

**MinIO endpoint**:

```
http://localhost:10001
```

**Console UI**: http://localhost:10002

When multiple `object_store` capability providers are installed, MinIO is the
deterministic default (SP9-DECISION-03); set `PHLO_OBJECT_STORE=minio` only to
make the choice explicit for integrations that resolve the active object store
via capabilities, such as `phlo-sling` auto-connections.

RustFS S3-compatible object storage:

```bash
# Host and ports
RUSTFS_HOST=rustfs
RUSTFS_API_PORT=9000
RUSTFS_CONSOLE_PORT=9001

# Credentials
RUSTFS_ACCESS_KEY=rustfsadmin
RUSTFS_SECRET_KEY=rustfsadmin
```

**RustFS endpoint**:

```
http://rustfs:9000
```

**Console UI**: http://localhost:9001

When multiple `object_store` capability providers are installed, MinIO is the
deterministic default (SP9-DECISION-03): with no explicit choice, capability
resolution always selects MinIO. Set `PHLO_OBJECT_STORE=rustfs` to select
RustFS explicitly for integrations that resolve the active object store via
capabilities.

### Catalog Configuration

Nessie Git-like catalog:

```bash
# Version and connectivity
NESSIE_VERSION=0.107.2
NESSIE_PORT=10003
NESSIE_HOST=localhost
NESSIE_API_VERSION=v1
```

**API endpoints**:

- v1 API: `http://localhost:10003/api/v1`
- v2 API: `http://localhost:10003/api/v2`
- Iceberg REST: `http://localhost:10003/iceberg`

### Query Engine Configuration

Trino distributed SQL engine:

```bash
# Version and connectivity
TRINO_VERSION=477
TRINO_PORT=10005
TRINO_HOST=localhost

# Catalog
TRINO_CATALOG=iceberg
```

**Connection string**:

```
trino://localhost:10005/iceberg_dev
```

### ClickHouse Configuration

ClickHouse analytical database for data plane:

```bash
# Image and connectivity
CLICKHOUSE_IMAGE=clickhouse/clickhouse-server:26.5.6.64-alpine@sha256:446c9d82443b926a5aacb952448dd632672606acc691ce1b3c2292b68a1197c2
CLICKHOUSE_HTTP_PORT=8123
CLICKHOUSE_NATIVE_PORT=19000
CLICKHOUSE_HOST=clickhouse

# Credentials
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=

# Database
CLICKHOUSE_DB=default

# TLS
CLICKHOUSE_SECURE=false
```

**HTTP endpoint**:

```
http://clickhouse:8123
```

**Native endpoint**:

```
clickhouse:19000
```

**Default databases** (created by clickhouse-setup):

- `raw` - Raw ingestion tables
- `staging` - Intermediate tables
- `curated` - Cleaned/validated tables
- `marts` - Published analytical marts

### API Backend Configuration

Observatory API runtime routing:

```bash
PHLO_API_PORT=4000
HOST=0.0.0.0
PHLO_AUTHORIZATION_BACKEND=
PHLO_AUTHORIZATION_MODE=optional
PHLO_QUERY_ENGINE_URL=
PHLO_QUERY_CATALOG=
PHLO_DEFAULT_REF=
PHLO_API_DISCOVERY_SCHEMAS=
```

Notes:

- `PHLO_AUTHORIZATION_MODE=optional` keeps guarded `phlo-api` routes reachable when no authz backend is configured.
- `PHLO_AUTHORIZATION_MODE=required` makes guarded `phlo-api` routes fail closed with HTTP `503` until `PHLO_AUTHORIZATION_BACKEND` resolves.
- The same settings can be declared in `phlo.yaml` under `api.authorization` or `services.phlo-api.authorization`.
- `PHLO_QUERY_ENGINE_URL` is required unless the resolved `query_engine` capability exposes `url`, `http_url`, or `host`/`port` metadata.
- `PHLO_QUERY_CATALOG` is required unless the resolved `query_engine` capability exposes `default_catalog`.
- `PHLO_DEFAULT_REF` is required for ref-dependent endpoints unless the resolved `query_engine` capability exposes `default_ref`.
- `PHLO_API_DISCOVERY_SCHEMAS` is optional only when table discovery can use request `branch`/`preferred_schema` values or `query_engine` capability metadata such as `discovery_schemas`.

### dbt Runtime Configuration

Generated dbt profile settings:

```bash
DBT_PROJECT_DIR=workflows/transforms/dbt
DBT_PROFILES_DIR=workflows/transforms/dbt/profiles
DBT_QUERY_ENGINE_TYPE=trino
DBT_QUERY_HOST=trino
DBT_QUERY_PORT=8080
DBT_QUERY_CATALOG=iceberg
DBT_QUERY_SCHEMA=raw
DBT_QUERY_USER=dagster
DBT_QUERY_HTTP_SCHEME=http
DBT_QUERY_AUTH_METHOD=none
DBT_QUERY_THREADS=2
```

These values are used to generate `profiles.yml` for dbt runtime execution.
Target and ref selection are derived from canonical runtime routing.

### Data Lake Configuration

Apache Iceberg table format:

```bash
# Storage paths
ICEBERG_WAREHOUSE_PATH=s3://lake/warehouse
ICEBERG_STAGING_PATH=s3://lake/stage

# Default namespace
ICEBERG_DEFAULT_NAMESPACE=raw

# Default catalog reference
ICEBERG_DEFAULT_REF=main

# Iceberg REST catalog endpoint
ICEBERG_CATALOG_URI=http://nessie:19120/iceberg
```

**Warehouse paths by branch**:

```python
# main branch
s3://lake/warehouse

# Custom branch
s3://lake/warehouse@feature-branch
```

### Delta Lake Configuration

Delta Lake table format (alternative to Iceberg):

```bash
# Storage paths
DELTA_WAREHOUSE_PATH=s3://lake/warehouse/delta
DELTA_STAGING_PATH=s3://lake/stage

# Default namespace
DELTA_DEFAULT_NAMESPACE=raw

# S3 endpoint
DELTA_S3_ENDPOINT=http://localhost:10001

# Allow unsafe rename for S3
DELTA_S3_ALLOW_UNSAFE_RENAME=true
```

### Branch Management

Nessie branch lifecycle configuration:

```bash
# Retention periods (days)
BRANCH_RETENTION_DAYS=7
BRANCH_RETENTION_DAYS_FAILED=2

# Automation
AUTO_PROMOTE_ENABLED=true
BRANCH_CLEANUP_ENABLED=false
```

**Behavior**:

- `BRANCH_RETENTION_DAYS`: Days to keep successful pipeline branches
- `BRANCH_RETENTION_DAYS_FAILED`: Days to keep failed pipeline branches
- `AUTO_PROMOTE_ENABLED`: Auto-merge to main when quality checks pass
- `BRANCH_CLEANUP_ENABLED`: Automatically delete old branches

### WAP Sensor Configuration

Enable the branch-first WAP policy at project scope in `phlo.yaml`:

```yaml
wap:
  enabled: true
  # These defaults target the standard generated Dagster asset job.
  job_name: __ASSET_JOB
  # Optional selectors for projects with more than one Dagster code location.
  repository_location_name: user_code
  repository_name: user_repository
  # Optional only when Dagster is remote; local Dagster is resolved by Phlo.
  dagster_url: https://dagster.example.com/graphql
```

When `enabled` is true, `phlo materialize` and `phlo backfill` always launch
through Dagster GraphQL after creating a fresh WAP branch. Do not pass WAP
command-line flags: the configuration is intentionally project-wide. Disabled
or absent configuration keeps the direct CLI execution path.

For a standard local Phlo stack, only `wap.enabled: true` is needed. Phlo uses
`resolve_host` and the project's `DAGSTER_PORT` to find local Dagster; do not
duplicate its URL in `phlo.yaml`.

WAP configuration is fail-closed: unknown `wap` keys and insecure non-local
`http://` endpoints are rejected. Remote Dagster endpoints must use HTTPS and
require `PHLO_DAGSTER_ACCESS_TOKEN`; plaintext HTTP is allowed only for the
local default endpoint.

Service-specific Docker settings such as `extra_hosts` belong under
`infrastructure.services` (the legacy top-level `services` form remains
supported):

```yaml
infrastructure:
  services:
    dagster:
      extra_hosts:
        - host.docker.internal:host-gateway
```

Dagster WAP sensor intervals:

```bash
PHLO_WAP_BRANCH_CREATION_INTERVAL_SECONDS=30
PHLO_WAP_PROMOTION_INTERVAL_SECONDS=60
PHLO_WAP_CLEANUP_INTERVAL_SECONDS=3600
```

These settings only matter when the active profile includes a versioned catalog
capability.
The project-level `wap.enabled` setting controls whether these sensors are
loaded. Each WAP launch first writes `.phlo/wap-reports/<logical-run-id>.json`.
The immutable launch manifest binds the logical ID, Dagster run ID, branch,
ref tags, and source/target hashes. It is retained when launch outcome is
rejected or ambiguous, and is verified again before promotion or retention
cleanup.

### Validation Configuration

Data quality validation settings:

```bash
# Freshness blocking
FRESHNESS_BLOCKS_PROMOTION=false

# Pandera validation level
PANDERA_CRITICAL_LEVEL=error  # error, warning, or skip

# Validation retry
VALIDATION_RETRY_ENABLED=true
VALIDATION_RETRY_MAX_ATTEMPTS=3
VALIDATION_RETRY_DELAY_SECONDS=300  # seconds
```

**Pandera levels**:

- `error`: Validation failures block pipeline
- `warning`: Log warnings but continue
- `skip`: Skip validation entirely (not recommended)

### Service Configuration

#### Superset

Business intelligence and visualization:

```bash
SUPERSET_PORT=10007
SUPERSET_ADMIN_USER=admin
SUPERSET_ADMIN_PASSWORD=admin
SUPERSET_ADMIN_EMAIL=admin@superset.com
SUPERSET_DATABASE_NAME=
```

Access: http://localhost:10007

`SUPERSET_DATABASE_NAME` is required unless a resolved `query_engine` capability
declares catalog metadata.

#### Dagster

Orchestration platform:

```bash
DAGSTER_PORT=10006

# Executor configuration (set only one)
PHLO_FORCE_IN_PROCESS_EXECUTOR=false   # Force in-process executor
PHLO_FORCE_MULTIPROCESS_EXECUTOR=false # Force multiprocess executor

# Host platform (auto-detected, but can be set explicitly for daemon/webserver on macOS)
PHLO_HOST_PLATFORM=  # Darwin, Linux, or Windows
```

Access: http://localhost:10006

#### Hub/Flask

Internal API server:

```bash
HUB_APP_PORT=10009
HUB_DEBUG=false
```

### Integration Services

#### API Layer

JWT authentication:

```bash
JWT_SECRET_KEY=your-secret-key-change-this-in-production
JWT_ALGORITHM=HS256
JWT_EXPIRATION_HOURS=24
```

#### Hasura GraphQL

```bash
HASURA_IMAGE=hasura/graphql-engine:v2.49.5@sha256:a9f427a9078b75c5f43ea40abd4ba4e426f45777f862eff7265f411a5ac96086
HASURA_GRAPHQL_PORT=10012
HASURA_GRAPHQL_ADMIN_SECRET=hasura-admin-secret
HASURA_GRAPHQL_ENABLE_CONSOLE=true
```

Access: http://localhost:10012

#### PostgREST

```bash
POSTGREST_PORT=10011
POSTGREST_DB_SCHEMA=marts
POSTGREST_DB_ANON_ROLE=web_anon
DBT_API_SOURCE_SCHEMA=
```

Access: http://localhost:10011

`DBT_API_SOURCE_SCHEMA` is optional only when the dbt manifest contains exactly one
model schema; otherwise it must be set explicitly.

#### OpenMetadata

Data catalog and governance:

```bash
OPENMETADATA_HOST=openmetadata-server
OPENMETADATA_PORT=8585
OPENMETADATA_HEAP_OPTS="-Xmx512m -Xms512m"
OPENMETADATA_ES_JAVA_OPTS="-Xms512m -Xmx512m"
OPENMETADATA_USERNAME=admin
OPENMETADATA_PASSWORD=admin
OPENMETADATA_VERIFY_SSL=false
OPENMETADATA_SERVICE_TYPE=
OPENMETADATA_CATALOG_SCANNER=
OPENMETADATA_QUERY_ENGINE=
OPENMETADATA_DATABASE_NAME=
OPENMETADATA_DBT_MANIFEST_PATH=workflows/transforms/dbt/target/manifest.json
OPENMETADATA_DBT_CATALOG_PATH=workflows/transforms/dbt/target/catalog.json
OPENMETADATA_SYNC_ENABLED=true
OPENMETADATA_SYNC_INTERVAL_SECONDS=300  # Minimum interval between syncs
```

`OPENMETADATA_DATABASE_NAME` and `OPENMETADATA_SERVICE_TYPE` are required unless a
resolved `query_engine` capability declares both catalog and `service_type`
metadata.

Access: http://localhost:8585

### Observability Stack

#### Prometheus

Metrics collection:

```bash
PROMETHEUS_PORT=9090
PROMETHEUS_PUBLIC_URL=
PROMETHEUS_QUERY_PATH=/graph
```

Access: http://localhost:9090

#### Loki

Log aggregation:

```bash
LOKI_PORT=3100
LOKI_PUBLIC_URL=
LOKI_LOGS_PATH=/logs
```

#### Grafana

Dashboards and visualization:

```bash
GRAFANA_PORT=3000
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=admin
GRAFANA_PUBLIC_URL=
GRAFANA_DASHBOARD_PATH_TEMPLATE=/d/{uid}
```

Access: http://localhost:3000

#### Observability Link Resolution

```bash
PHLO_OBSERVABILITY_PUBLIC_HOST=localhost
PHLO_OBSERVABILITY_PUBLIC_SCHEME=http
```

- Set `*_PUBLIC_URL` when Grafana, Loki, or Prometheus are exposed behind a proxy or custom domain.
- If `*_PUBLIC_URL` is unset, Phlo builds links from `PHLO_OBSERVABILITY_PUBLIC_SCHEME`, `PHLO_OBSERVABILITY_PUBLIC_HOST`, and the configured service port.

### Alerting Configuration

#### Slack

```bash
PHLO_ALERT_SLACK_WEBHOOK=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
PHLO_ALERT_SLACK_CHANNEL=#data-alerts
```

#### PagerDuty

```bash
PHLO_ALERT_PAGERDUTY_KEY=your-integration-key
```

#### Email (SMTP)

```bash
PHLO_ALERT_EMAIL_SMTP_HOST=smtp.gmail.com
PHLO_ALERT_EMAIL_SMTP_PORT=587
PHLO_ALERT_EMAIL_SMTP_USER=your-email@gmail.com
PHLO_ALERT_EMAIL_SMTP_PASSWORD=your-app-password
PHLO_ALERT_EMAIL_RECIPIENTS=team@yourdomain.com,alerts@yourdomain.com  # Comma-separated list
```

### Security Configuration

See [Security Setup Guide](../setup/security.md) for detailed setup instructions.

#### Trino Authentication

```bash
# Authentication type (PASSWORD, OAUTH2, JWT, CERTIFICATE, KERBEROS, or empty)
TRINO_AUTH_TYPE=

# LDAP Authentication (when TRINO_AUTH_TYPE=PASSWORD)
TRINO_LDAP_URL=ldaps://ldap.example.com:636
TRINO_LDAP_USER_BIND_PATTERN=${USER}@example.com

# OAuth2/OIDC Authentication (when TRINO_AUTH_TYPE=OAUTH2)
TRINO_OAUTH2_ISSUER=https://auth.example.com
TRINO_OAUTH2_CLIENT_ID=trino
TRINO_OAUTH2_CLIENT_SECRET=your-client-secret

# HTTPS/TLS
TRINO_HTTPS_ENABLED=false
TRINO_HTTPS_KEYSTORE_PATH=/etc/trino/keystore.jks
TRINO_HTTPS_KEYSTORE_PASSWORD=keystore-password

# Access Control
TRINO_ACCESS_CONTROL_TYPE=file
TRINO_ACCESS_CONTROL_CONFIG_FILE=/etc/trino/access-control.json
```

#### Nessie Authentication

```bash
# OIDC/OAuth2 Authentication
NESSIE_OIDC_ENABLED=false
NESSIE_OIDC_SERVER_URL=https://auth.example.com/realms/phlo
NESSIE_OIDC_CLIENT_ID=nessie
NESSIE_OIDC_CLIENT_SECRET=your-client-secret
NESSIE_OIDC_ISSUER=https://auth.example.com

# Authorization
NESSIE_AUTHZ_ENABLED=false
```

#### MinIO Security

```bash
# TLS (set server URL to enable HTTPS)
MINIO_SERVER_URL=https://minio.example.com

# OIDC Authentication
MINIO_OIDC_CONFIG_URL=https://auth.example.com/.well-known/openid-configuration
MINIO_OIDC_CLIENT_ID=minio
MINIO_OIDC_CLIENT_SECRET=your-client-secret
MINIO_OIDC_CLAIM_NAME=policy
MINIO_OIDC_SCOPES=openid

# LDAP Authentication
MINIO_LDAP_SERVER=ldap.example.com:636
MINIO_LDAP_BIND_DN=cn=admin,dc=example,dc=com
MINIO_LDAP_BIND_PASSWORD=ldap-password
MINIO_LDAP_USER_BASE_DN=ou=users,dc=example,dc=com
MINIO_LDAP_USER_FILTER=(uid=%s)

# Encryption at Rest
MINIO_AUTO_ENCRYPTION=off

# Audit Logging
MINIO_AUDIT_ENABLED=off
MINIO_AUDIT_ENDPOINT=http://audit-service:8080/logs
```

`MINIO_AUDIT_ENABLED` and `MINIO_AUDIT_ENDPOINT` are Phlo's bundled audit-log automation surface for object storage events. In production, point the endpoint at a durable backend and pair it with centralized application logs. See [Audit Logging](../operations/audit-logging.md).

#### PostgreSQL SSL

```bash
# SSL Mode (disable, allow, prefer, require, verify-ca, verify-full)
POSTGRES_SSL_MODE=prefer
POSTGRES_SSL_CERT_FILE=/path/to/cert.pem
POSTGRES_SSL_KEY_FILE=/path/to/key.pem
POSTGRES_SSL_CA_FILE=/path/to/ca.pem
```

### dbt Configuration

```bash
# dbt artifact paths (defaults to <DBT_PROJECT_DIR>/target when unset)
DBT_MANIFEST_PATH=workflows/transforms/dbt/target/manifest.json
DBT_CATALOG_PATH=workflows/transforms/dbt/target/catalog.json

# dbt project directory
DBT_PROJECT_DIR=workflows/transforms/dbt

# Workflows path (for external projects)
WORKFLOWS_PATH=workflows
```

### Plugin Configuration

```bash
# Plugin system
PLUGINS_ENABLED=true
PLUGINS_AUTO_DISCOVER=true

# Whitelist/blacklist (comma-separated)
PLUGINS_WHITELIST=plugin1,plugin2
PLUGINS_BLACKLIST=deprecated_plugin

# Plugin registry
PLUGIN_REGISTRY_URL=https://registry.phlohouse.com/plugins.json
PLUGIN_REGISTRY_CACHE_TTL_SECONDS=3600
PLUGIN_REGISTRY_TIMEOUT_SECONDS=10
```

`PLUGINS_AUTO_DISCOVER` is the default switch. `PHLO_NO_AUTO_DISCOVER` has disable
override precedence at runtime:

- Truthy values disable auto-discovery (`1`, `true`, `yes`, `on`).
- Falsy values do not disable (`0`, `false`, `no`, `off`).
- Any other non-empty value is treated as disable and logged as invalid.

```bash
# Disabled (env override wins)
PLUGINS_AUTO_DISCOVER=true
PHLO_NO_AUTO_DISCOVER=1

# Enabled (falsy env does not disable)
PLUGINS_AUTO_DISCOVER=true
PHLO_NO_AUTO_DISCOVER=0

# Disabled (settings already false; env cannot force enable)
PLUGINS_AUTO_DISCOVER=false
PHLO_NO_AUTO_DISCOVER=0
```

## Infrastructure Configuration (phlo.yaml)

Project-level configuration in `phlo.yaml`:

```yaml
name: my-project
description: My data lakehouse project

infrastructure:
  # Local container backend for service lifecycle commands: docker, podman, or auto
  container_backend: docker

  # Container naming pattern
  container_naming_pattern: "{{project}}-{{service}}-1"

  # Optional runtime service-name metadata for commands that inspect containers
  services:
    dagster:
      container_name: null # Use pattern
      service_name: dagster
      host: localhost
      internal_host: dagster

    postgres:
      container_name: null
      service_name: postgres
      host: localhost
      internal_host: postgres
```

Service enablement, Compose port replacement, environment, volume, `extra_hosts`, dependency,
command, and healthcheck overrides live in the top-level `services:` section, not
under `infrastructure.services`.

Use `extra_hosts` when a generated service must resolve a host-private dependency from
inside Docker. Values use Docker Compose host mappings and replace any package default:

```yaml
services:
  dagster:
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

Service packages own their default ports. When a package is selected by
`phlo services init`, its non-secret defaults are materialized into `.phlo/.env`;
project-specific overrides belong in top-level `env:`.

### `infrastructure.container_backend`

Selects the local container backend used by `phlo services` commands.

Docker with Compose v2 is the currently supported path. Podman is documented as an
experimental alternative and is not CI-supported.

Allowed values:

- `docker`
- `podman`
- `auto`

Environment override:

```bash
PHLO_CONTAINER_BACKEND=podman
```

### Loading Infrastructure Config

```python
from phlo.infrastructure.config import (
    load_infrastructure_config,
    get_container_name,
    get_service_config
)

# Load config
config = load_infrastructure_config()

# Get container name
container = get_container_name("dagster-webserver")
# Returns: "my-project-dagster-webserver-1"

# Get service config
service = get_service_config("postgres")
# Returns: dict with host, port, credentials, etc.
```

## Python Configuration (Package Settings)

Programmatic access to configuration lives in each capability package:

```python
from phlo_postgres.settings import get_settings as get_postgres_settings
from phlo_minio.settings import get_settings as get_minio_settings
from phlo_nessie.settings import get_settings as get_nessie_settings
from phlo_trino.settings import get_settings as get_trino_settings
from phlo_iceberg.settings import get_settings as get_iceberg_settings

# Database
postgres = get_postgres_settings()
postgres.postgres_host
postgres.postgres_port
postgres.get_postgres_connection_string()

# MinIO
minio = get_minio_settings()
minio.minio_endpoint()

# Nessie
nessie = get_nessie_settings()
nessie.nessie_uri()
nessie.nessie_api_uri()
nessie.nessie_iceberg_rest_uri()

# Trino
trino = get_trino_settings()
trino.trino_connection_string()

# Iceberg
iceberg = get_iceberg_settings()
iceberg.iceberg_warehouse_path
iceberg.get_iceberg_warehouse_for_branch("main")
```

## Runtime Configuration

Dagster run configuration for asset execution:

```python
# Example run config
{
    "ops": {
        "my_asset": {
            "config": {
                "partition_date": "2025-01-15",
                "full_refresh": false
            }
        }
    },
    "resources": {
        "iceberg": {
            "config": {
                "ref": "pipeline-run-abc123"
            }
        }
    }
}
```

## Port Reference

Standard port assignments:

```
10000  PostgreSQL
10001  MinIO API
10002  MinIO Console
10003  Nessie
10005  Trino
10006  Dagster
10007  Superset
10009  Hub/Flask
10011  PostgREST
10012  Hasura GraphQL
8585   OpenMetadata
3000   Grafana
9090   Prometheus
3100   Loki
```

## Environment-Specific Configurations

### Development

```yaml
# phlo.yaml (development)
env:
  POSTGRES_HOST: localhost
  MINIO_HOST: localhost
  DAGSTER_HOST_PLATFORM: local
  HUB_DEBUG: true
  AUTO_PROMOTE_ENABLED: true
  BRANCH_CLEANUP_ENABLED: false
```

### Staging

```yaml
# phlo.staging.yaml
env:
  POSTGRES_HOST: postgres-staging
  MINIO_HOST: minio-staging
  DAGSTER_HOST_PLATFORM: docker
  HUB_DEBUG: false
  AUTO_PROMOTE_ENABLED: true
  BRANCH_CLEANUP_ENABLED: true
  BRANCH_RETENTION_DAYS: 3
```

### Production

```yaml
# phlo.production.yaml
env:
  POSTGRES_HOST: postgres-prod.internal
  POSTGRES_PORT: 5432
  MINIO_HOST: minio-prod.internal
  NESSIE_HOST: nessie-prod.internal
  TRINO_HOST: trino-prod.internal
  DAGSTER_HOST_PLATFORM: k8s
  DAGSTER_EXECUTOR: multiprocess
  HUB_DEBUG: false
  AUTO_PROMOTE_ENABLED: true
  BRANCH_CLEANUP_ENABLED: true
  BRANCH_RETENTION_DAYS: 7
  BRANCH_RETENTION_DAYS_FAILED: 2
  FRESHNESS_BLOCKS_PROMOTION: true
  PANDERA_CRITICAL_LEVEL: error
  VALIDATION_RETRY_ENABLED: true
  OPENMETADATA_SYNC_ENABLED: true
```

## Security Best Practices

### Secrets Management

Do not commit secrets to version control:

```bash
# .gitignore
.phlo/.env.local
```

Use environment-specific files:

```bash
.env.example      # Secrets template (committed)
.phlo/.env.local  # Local secrets (ignored)
```

### Strong Passwords

Generate secure passwords:

```bash
# Generate random password
openssl rand -base64 32

# Use in .phlo/.env.local
POSTGRES_PASSWORD=<generated-password>
MINIO_ROOT_PASSWORD=<generated-password>
JWT_SECRET_KEY=<generated-password>
```

### Minimal Permissions

Use least-privilege principle:

```bash
# Read-only user for BI tools
POSTGRES_BI_USER=bi_readonly
POSTGRES_BI_PASSWORD=<password>

# Grant only SELECT on marts
GRANT SELECT ON SCHEMA marts TO bi_readonly;
```

## Configuration Validation

### Validate with CLI

```bash
# Validate phlo.yaml
phlo config validate phlo.yaml

# Show current config
phlo config show

# Show with secrets (masked by default)
phlo config show --secrets
```

### Validation in Python

```python
from phlo_postgres.settings import get_settings
from pydantic import ValidationError

try:
    # Access settings (validates on load)
    conn_str = get_settings().get_postgres_connection_string()
except ValidationError as e:
    print(f"Configuration error: {e}")
```

## Troubleshooting

### Connection Issues

```bash
# Test PostgreSQL
psql postgresql://postgres:password@localhost:10000/cascade

# Test MinIO
mc alias set local http://localhost:10001 minioadmin minioadmin
mc ls local

# Test Nessie
curl http://localhost:10003/api/v2/config

# Test Trino
phlo trino
```

### Port Conflicts

Check if ports are in use:

```bash
# macOS/Linux
lsof -i :10000
lsof -i :10006

# Windows
netstat -ano | findstr :10000
```

Change ports in `phlo.yaml` (env:):

```bash
POSTGRES_PORT=15432
DAGSTER_PORT=13000
```

### Permission Errors

Fix Docker volume permissions:

```bash
sudo chown -R $USER:$USER .phlo/
chmod -R 755 .phlo/
```

## Next Steps

- [Installation Guide](../getting-started/installation.md) - Setup instructions
- [CLI Reference](cli-reference.md) - Command-line tools
- [Developer Guide](../guides/developer-guide.md) - Building workflows
- [Troubleshooting](../operations/troubleshooting.md) - Common issues
