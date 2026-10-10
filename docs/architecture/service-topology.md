# Service topology

Service manifests are owned by provider packages and combined by the compose generator. `default: true` controls default selection; it does not establish support. The support authority is [`registry/support/v1.json`](../../registry/support/v1.json).

## Typed authority and projections

[`ServiceDefinition`](../../src/phlo/plugins/discovery/_service_definition.py) is
the single typed topology authority. Provider packages author its existing YAML
input, including primary `service.yaml` files and companion or explicitly
registered manifests. There is no second central registry of service names.
Identities remain open strings so third-party providers can declare services.

The authority owns service identity, top-level `depends_on`, and typed
`ServicePort` values parsed from `compose.ports`. Ports carry integer container
and host defaults, the override variable, bind address, and protocol. The CLI
ports command uses the same parser. Dependency discovery and selected-service
Compose generation use the same `ServiceDefinition` objects.

The [generated topology reference](../reference/service-topology.md) and its
[JSON projection](../reference/generated/service-topology.json) cover all
first-party primary and companion manifests, including explicitly registered
PostgreSQL exporter and volume setup files. They are outputs, not authorities.
Change topology in the owning package, then run `make docs-reference-generate`.
Do not edit generated projections by hand.

`make docs-reference-check`, already required by CI's Python lane and
`make check`, compares manifests with the committed projections without writing
them. A valid identity, port, or dependency change fails until its projections
are deliberately regenerated and reviewed. The generator also rejects duplicate
identities, unknown first-party dependencies, cycles, conflicting
`compose.depends_on`, and disagreement between host-port fallback values and
`env_vars` defaults. Regeneration cannot conceal those invalid declarations.
Negative tests mutate real `service.yaml` copies in each topology dimension and
assert that the read-only check fails without changing the saved projection.

These are startup dependencies, not every runtime network call. For example,
the API has a PostgreSQL startup dependency but can call optional orchestrator
and query providers. Global validation covers the first-party inventory, not
external provider identities. Project selection, deployment overrides, native
mode, and production exposure rules remain the Compose generator's concern.
Generated Compose is authoritative for that selected deployment, not a
competing authored topology.

## Core and API topology

```text
postgres-volume-setup -> postgres ----+
minio-volume-setup ---> minio -------+----> nessie ----> trino ----> dagster
                                     |                     |
                                     +---------------------+
                        minio ----> minio-setup

postgres ---------------------------> phlo-api ----> observatory
```

Setup and companion services can add edges not shown in a package's primary `service.yaml`. The generated compose model is authoritative for a selected package set.

All blessed-core services in this table currently have alpha maturity and blocked release gates. “Blessed core” is a target profile, not a production-readiness claim; see the [support matrix](../reference/support-matrix.md).

Dependencies and ports come from the generated reference above. This table
records state and readiness responsibilities rather than duplicating topology.

| Service | Package owner; target profile | Persistent or mounted state | Health |
| --- | --- | --- | --- |
| `postgres-volume-setup` | `phlo-postgres`; blessed core | Prepares `postgres-data` | One-shot completion |
| `postgres` | `phlo-postgres`; blessed core, default | `postgres-data:/var/lib/postgresql` | `pg_isready` |
| `minio-volume-setup` | `phlo-minio`; blessed core | Prepares `minio-data` | One-shot completion |
| `minio` | `phlo-minio`; blessed core, default | `minio-data:/bitnami/minio/data` | `/minio/health/ready` |
| `minio-setup` | `phlo-minio`; blessed core | Creates required buckets | One-shot completion |
| `nessie` | `phlo-nessie`; blessed core, default | Catalogue state in PostgreSQL; read-only `./nessie` authorisation config; warehouse in MinIO | `/api/v1/config` |
| `trino` | `phlo-trino`; blessed core, default | Generated `./trino` configuration, mounted read/write | `/v1/info/state` must be `ACTIVE` |
| `dagster` | `phlo-dagster`; blessed core, default | `./dagster:/opt/dagster` and project at `/app` | `/server_info` |
| `dagster-daemon` | `phlo-dagster`; blessed core | Shares Dagster home and PostgreSQL run storage | Process/service health from generated compose |
| `phlo-api` | `phlo-api`; blessed core, `api` profile | Read-only project plus writable `.phlo/observatory`, `.phlo/state`, and logs | `/health` |
| `observatory` | `phlo-observatory`; blessed core, `api` profile | No service-owned named volume | `/healthz` |

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

The generated reference lists optional host-port defaults and their override
variables. Services without a host mapping, such as oauth2-proxy in its primary
manifest, remain reachable only on the Compose network unless another component
publishes them.

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
