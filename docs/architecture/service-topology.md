# Service topology

Service manifests are owned by provider packages and combined by the compose generator. `default: true` controls default selection; it does not establish support. The support authority is [`registry/support/v1.json`](../../registry/support/v1.json).

## Core and API topology

```text
postgres-volume-setup -> postgres ----+
minio-setup ----------> minio -------+----> nessie ----> trino ----> dagster
                                     |                     |
                                     +---------------------+

postgres ---------------------------> phlo-api ----> observatory
```

Setup and companion services can add edges not shown in a package's primary `service.yaml`. The generated compose model is authoritative for a selected package set.

All blessed-core services in this table currently have alpha maturity and blocked release gates. “Blessed core” is a target profile, not a production-readiness claim; see the [support matrix](../reference/support-matrix.md).

| Service | Package owner; target profile | Dependencies | Default host port | Persistent or mounted state | Health |
| --- | --- | --- | --- | --- | --- |
| `postgres-volume-setup` | `phlo-postgres`; blessed core | None | None | Prepares `postgres-data` | One-shot completion |
| `postgres` | `phlo-postgres`; blessed core, default | volume setup | `10000` to 5432 | `postgres-data:/var/lib/postgresql` | `pg_isready` |
| `minio-volume-setup` | `phlo-minio`; blessed core | None | None | Prepares `minio-data` | One-shot completion |
| `minio` | `phlo-minio`; blessed core, default | volume setup | `10001` API, `10002` console | `minio-data:/bitnami/minio/data` | `/minio/health/ready` |
| `minio-setup` | `phlo-minio`; blessed core | MinIO | None | Creates required buckets | One-shot completion |
| `nessie` | `phlo-nessie`; blessed core, default | PostgreSQL, MinIO | `10003` | Catalogue state in PostgreSQL; read-only `./nessie` authorisation config; warehouse in MinIO | `/api/v1/config` |
| `trino` | `phlo-trino`; blessed core, default | Nessie, MinIO | `10005` | Generated `./trino` configuration, mounted read/write | `/v1/info/state` must be `ACTIVE` |
| `dagster` | `phlo-dagster`; blessed core, default | PostgreSQL, MinIO, Nessie, Trino | `10006` | `./dagster:/opt/dagster` and project at `/app` | `/server_info` |
| `dagster-daemon` | `phlo-dagster`; blessed core | Same data services | None | Shares Dagster home and PostgreSQL run storage | Process/service health from generated compose |
| `phlo-api` | `phlo-api`; blessed core, `api` profile | PostgreSQL | `4000` | Read-only project plus writable `.phlo/observatory`, `.phlo/state`, and logs | `/health` |
| `observatory` | `phlo-observatory`; blessed core, `api` profile | phlo-api; runtime calls Dagster, Nessie, and Trino | `3001` | No service-owned named volume | `/` |

The principal manifests are [PostgreSQL](../../packages/phlo-postgres/src/phlo_postgres/service.yaml), [MinIO](../../packages/phlo-minio/src/phlo_minio/service.yaml), [Nessie](../../packages/phlo-nessie/src/phlo_nessie/service.yaml), [Trino](../../packages/phlo-trino/src/phlo_trino/service.yaml), [Dagster](../../packages/phlo-dagster/src/phlo_dagster/service.yaml), [API](../../packages/phlo-api/src/phlo_api/service.yaml), and [Observatory](../../packages/phlo-observatory/src/phlo_observatory/service.yaml).

## Optional services

| Support tier | Services and profile | Main dependency or state |
| --- | --- | --- |
| Optional target: supported; current: alpha | `hasura`, `postgrest` (`api`) | PostgreSQL; Hasura metadata and exposed schemas live there |
| Optional target: supported; current: alpha | `oauth2-proxy`, `traefik` (`proxy`) | Identity provider and Docker socket respectively; no Phlo backup contribution |
| Preview | `airbyte` and its Temporal, manifest, and bootloader companions | Its package-defined databases and volumes |
| Preview | `alloy`, `grafana`, `loki`, `prometheus`, `clickstack`, `phlo-observer` and DB setup (`observability`) | Metrics, logs, traces, and observer databases/volumes |
| Preview | `clickhouse` plus setup, `kafka`, `polaris`, `rustfs` plus setup and volume setup | Alternative data services with package-owned volumes |
| Preview | `openmetadata` plus setup, MySQL, and Elasticsearch (`openmetadata`) | Metadata, search, and database volumes |
| Preview | `superset` | Superset metadata and application state |
| Development only | `pgweb` | Direct PostgreSQL browser; blocked in regulated mode |

Some optional manifests currently say `default: true`, notably Superset and pgweb. That flag affects selection only. Their preview and development-only support tiers still apply. Select profiles explicitly and inspect `phlo services config` or the generated compose before deployment.

Common optional host ports are Hasura `8082`, PostgREST `3002`, Traefik `80`, Observatory `3001`, API `4000`, Prometheus `9090`, Grafana `3000`, and ClickHouse HTTP `8123`; every service manifest exposes its own environment-variable override. Services without a host mapping, such as oauth2-proxy in its primary manifest, remain reachable only on the compose network unless another component publishes them.

## Credentials and network exposure

Development manifests provide fallback credentials so a local stack can start. PostgreSQL defaults to user/database/password `phlo`; MinIO defaults to user `minio` and its manifest-defined development password. Nessie, Trino, Dagster, Hasura, PostgREST, and the API consume those shared values unless service-specific credentials override them. OAuth2 proxy requires issuer, client, and cookie secrets.

Do not use fallback or shared credentials in production. The production compose renderer removes core host ports and routes and rejects default/shared credentials. Put secrets in the supported credential source, use service-specific principals, enable TLS and backend-native authentication, and expose browser services through the approved ingress. See the [production renderer](../../src/phlo/plugins/compose/generator.py) and [regulated surface inventory](regulated-surface-inventory.md).

## Backup ownership

Only four service authorities contribute to the v1 set:

- PostgreSQL contributes its configured database, including dependent state stored there.
- Nessie contributes branch/hash inventory.
- MinIO contributes all non-system bucket objects.
- Iceberg contributes table/snapshot inventory; object data and metadata come from MinIO.

Trino is stateless apart from generated configuration. Dagster and Observatory have no separate contributor; only their PostgreSQL-resident state is covered. API filesystem state, Dagster bind-mounted files, service configuration, credentials, and every optional service are outside the set. See [Continuity contract](../reference/continuity.md).

## Start and stop considerations

`phlo services start` resolves dependencies, starts compose services, waits for health, then runs post-start hooks such as Nessie branch initialisation and dbt compilation. One-shot setup services must complete successfully. Cold Dagster and Trino starts have extended health start periods; do not replace readiness with process-running checks.

Stop the same selected profiles you started. The stop command activates all discovered profiles when it needs to address the full generated project, which prevents profiled containers from being left behind. Normal stop preserves volumes. `phlo services stop --volumes` deletes persistent named-volume data and is not a restart operation. Back up state and confirm the target before using it.

Production generation deliberately changes exposure and credential requirements. Generate and validate the production compose rather than copying the development port and credential assumptions from this page.
