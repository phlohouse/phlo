# Regulated Surface Inventory

Canonical registry of all Phlo surfaces and their regulated-access classification.
Source of truth for `phlo.security.gating`.

## Enforcement Layers

The platform enforces access across four layers, not just at the UI/API boundary:

| Layer | Surfaces | Enforcement mechanism |
|-------|----------|----------------------|
| Control-plane enforced | phlo-api, CLI, Dagster webserver | Phlo `enforce()` at request boundary |
| Autonomous execution | Dagster daemon (schedules, sensors) | Platform principal (`platform:dagster-daemon`) + run-level audit |
| Data-plane governed | Trino, PostgreSQL, MinIO, Nessie | Compiled RBAC applied to backend-native auth; verified at startup |
| Ingress-gated read surfaces | Hasura, PostgREST, Superset | Ingress auth + compiled read-only permissions; writes blocked by default |
| Inherited protection | Observatory, dbt/dlt/sling libraries | Protected by upstream enforced surfaces |
| Blocked | pgweb, OpenMetadata (direct) | Startup gating prevents use |

### Principal Types

| Type | Subject pattern | When used |
|------|-----------------|-----------|
| `user` | `alice@example.com` | Human via IdP/proxy/JWT |
| `service` | `service:phlo-api` | Named service calling another service |
| `platform` | `platform:dagster-daemon` | Autonomous execution with no contemporaneous human request |

## Classification Tiers

| Tier | Label | Description |
|------|-------|-------------|
| 1 | Required direct surfaces | User/operator entrypoints; need `regulated_surface` adapters |
| 2 | Ingress-controlled surfaces | Browser-facing; protected upstream by ingress + regulated API |
| 3 | Backend-governed services | No request-time adapter; governed by compiled RBAC artifacts |
| 4 | Internal ops tools with CLIs | Operational tooling; backend-only access |
| 5 | Plugin/indirect packages | No direct entrypoint; reached only through already-regulated surfaces |

## Surface Registry

### Tier 1: Required Direct Surfaces

#### `phlo-api` (reference implementation)

- **Package:** `phlo-api`
- **Surface type:** REST/gRPC API
- **Enforcement mode:** `regulated_surface` adapter; first-class Phlo PDP
- **Identity source:** IdP via ingress proxy; service tokens
- **Audit path:** Canonical audit log via `phlo.audit`
- **Policy source:** Canonical RBAC compiled artifacts
- **Direct exposure:** Always direct
- **Adapter status:** Integrated (reference implementation)
- **Notes:** The only fully integrated regulated surface in v1.

---

#### `phlo-cli`

- **Package:** `phlo-cli`
- **Surface type:** CLI command-line operator surface
- **Enforcement mode:** `regulated_surface` adapter
- **Identity source:** Human: IdP / interactive session; Automation: service token
- **Audit path:** Canonical audit log via `phlo.audit`
- **Policy source:** Canonical RBAC compiled artifacts
- **Direct exposure:** Direct operator/admin access
- **Adapter status:** Integrated
- **gating.py entry:** `APPROVED_SERVICES` (`cli`)
- **Notes:** Only mutation-grade commands require enforcement. Read-only and help commands are not privileged.

---

#### `phlo-dagster`

- **Package:** `phlo-dagster`
- **Surface type:** Webserver + GraphQL API
- **Enforcement mode:** `regulated_surface` adapter
- **Identity source:** Dagster session tokens; extracted by middleware
- **Audit path:** Canonical audit log via `phlo.audit`
- **Policy source:** Canonical RBAC compiled artifacts
- **Direct exposure:** Direct user/admin access via dagster-webserver
- **Adapter status:** Integrated (Units 2 and 3 completed)
- **gating.py entry:** `APPROVED_SERVICES` (`dagster-webserver`, `dagster-daemon`)
- **Notes:** Dagster GraphQL operation classification and principal extraction implemented via webserver middleware.

---

### Tier 2: Ingress-Controlled Surfaces

#### `phlo-hasura`

- **Package:** `phlo-hasura`
- **Surface type:** GraphQL API
- **Enforcement mode:** Ingress-gated; protected by ingress + regulated API
- **Identity source:** Hasura session tokens; JWT via ingress
- **Audit path:** Via regulated upstream API
- **Policy source:** phlo-api PDP; ingress policy
- **Direct exposure:** Conditionally allowed — allowed if all server actions go through phlo-api
- **Adapter status:** No `regulated_surface` adapter needed
- **gating.py entry:** `INGRESS_OPTIONAL_SERVICES` (`hasura`)
- **Notes:** Protected upstream by ingress + regulated API. Should NOT duplicate policy decisions already made in phlo-api.

---

#### `phlo-postgrest`

- **Package:** `phlo-postgrest`
- **Surface type:** REST API (PostgreSQL proxy)
- **Enforcement mode:** Ingress-gated; protected by ingress + regulated API
- **Identity source:** Service tokens via ingress; row-level policies
- **Audit path:** Via regulated upstream API
- **Policy source:** phlo-api PDP; ingress policy; PostgreSQL RLS compiled from canonical RBAC
- **Direct exposure:** Conditionally allowed — allowed if all server actions go through phlo-api
- **Adapter status:** No `regulated_surface` adapter needed
- **gating.py entry:** `INGRESS_OPTIONAL_SERVICES` (`postgrest`)
- **Notes:** Same decision as Hasura: protected upstream by ingress + regulated API.

---

#### `phlo-observatory`

- **Package:** `phlo-observatory`
- **Surface type:** Web UI
- **Enforcement mode:** Ingress + upstream regulated API (phlo-api)
- **Identity source:** Ingress proxy IdP integration; session propagated to phlo-api
- **Audit path:** Via phlo-api regulated surface
- **Policy source:** phlo-api PDP; ingress policy
- **Direct exposure:** Conditionally allowed — allowed if all server actions go through phlo-api
- **Adapter status:** No `regulated_surface` adapter needed
- **gating.py entry:** `APPROVED_SERVICES` (`observatory`)
- **Notes:** Should NOT duplicate policy decisions already made in phlo-api. Primary boundary is ingress + phlo-api.

---

#### `phlo-superset`

- **Package:** `phlo-superset`
- **Surface type:** Web UI (analytical dashboards)
- **Enforcement mode:** Ingress-gated; not a Phlo-native PDP surface
- **Identity source:** Ingress IdP; external IdP integration
- **Audit path:** External IdP audit; superset audit logs
- **Policy source:** Ingress policy; superset native RBAC
- **Direct exposure:** Blocked in regulated mode unless ingress integration is explicitly documented
- **Adapter status:** No `regulated_surface` adapter planned
- **gating.py entry:** `INGRESS_OPTIONAL_SERVICES` (`superset`)
- **Notes:** Not a first-class Phlo PDP surface. Ingress/IdP policy is the primary boundary.

---

#### `phlo-pgweb`

- **Package:** `phlo-pgweb`
- **Surface type:** Web UI (PostgreSQL browser)
- **Enforcement mode:** Ingress-gated only; not core-regulated by custom PDP logic
- **Identity source:** Ingress proxy
- **Audit path:** Ingress audit
- **Policy source:** Ingress policy
- **Direct exposure:** Blocked in regulated mode
- **Adapter status:** No `regulated_surface` adapter needed; explicitly blocked
- **gating.py entry:** `UNSUPPORTED_SERVICES` (`pgweb`)
- **Notes:** Explicitly blocked in regulated mode due to direct Postgres access without Phlo auth mediation.

---

### Tier 3: Backend-Governed Services

These packages do not primarily need `regulated_surface` adapters. They need compiler quality and deployment gating.

#### `phlo-trino`

- **Package:** `phlo-trino`
- **Surface type:** Query engine backend
- **Enforcement mode:** Compiler-side (canonical RBAC compiled to Trino policies); verify/read-current-state coverage
- **Identity source:** Internal service identity; Trino catalog permissions
- **Audit path:** Compiler-emitted audit; Trino query logs
- **Policy source:** Canonical RBAC compiled artifacts
- **Direct exposure:** Not a direct user surface; backend-only
- **Adapter status:** No request-time adapter; governed by compiler
- **gating.py entry:** `APPROVED_SERVICES` (`trino`)

#### `phlo-nessie`

- **Package:** `phlo-nessie`
- **Surface type:** Catalog/versioning backend
- **Enforcement mode:** Compiler-side; verify/read-current-state coverage
- **Identity source:** Internal service identity
- **Audit path:** Compiler-emitted audit; nessie operation logs
- **Policy source:** Canonical RBAC compiled artifacts
- **Direct exposure:** Not a direct user surface; backend-only
- **Adapter status:** No request-time adapter; governed by compiler
- **gating.py entry:** `APPROVED_SERVICES` (`nessie`)

#### `phlo-minio`

- **Package:** `phlo-minio`
- **Surface type:** Object storage backend
- **Enforcement mode:** Compiler-side; verify/read-current-state coverage
- **Identity source:** Internal service identity; S3-style policies
- **Audit path:** Compiler-emitted audit; minio audit logs
- **Policy source:** Canonical RBAC compiled artifacts
- **Direct exposure:** Not a direct user surface; backend-only
- **Adapter status:** No request-time adapter; governed by compiler
- **gating.py entry:** `APPROVED_SERVICES` (`minio`, `minio-setup`)

#### `phlo-postgres`

- **Package:** `phlo-postgres`
- **Surface type:** Metadata store backend
- **Enforcement mode:** Compiler-side; verify/read-current-state coverage
- **Identity source:** Internal service identity
- **Audit path:** Compiler-emitted audit; postgres logs
- **Policy source:** Canonical RBAC compiled artifacts
- **Direct exposure:** Not a direct user surface; backend-only
- **Adapter status:** No request-time adapter; governed by compiler
- **gating.py entry:** `APPROVED_SERVICES` (`postgres`)

---

### Tier 4: Internal Ops Tools with CLIs

These packages expose operational CLIs but are backend-infrastructure only.

#### `phlo-clickhouse`

- **Package:** `phlo-clickhouse`
- **Surface type:** OLAP database backend
- **Enforcement mode:** Compiler-side; internal service identity
- **Identity source:** Internal service identity
- **Audit path:** Compiler-emitted audit; clickhouse query logs
- **Policy source:** Canonical RBAC compiled artifacts
- **Direct exposure:** Not a direct user surface; backend-only
- **Adapter status:** No request-time adapter; governed by compiler
- **gating.py entry:** `APPROVED_SERVICES` (`clickhouse`, `clickhouse-setup`)

#### `phlo-clickstack`

- **Package:** `phlo-clickstack`
- **Surface type:** ClickHouse management CLI
- **Enforcement mode:** N/A — operational tooling
- **Identity source:** Internal service identity
- **Audit path:** Via orchestration surface
- **Policy source:** Orchestration PDP
- **Direct exposure:** No direct entrypoint; ops tooling only
- **Adapter status:** Excluded — operational tooling only

#### `phlo-sling`

- **Package:** `phlo-sling`
- **Surface type:** Data replication CLI
- **Enforcement mode:** N/A — operational tooling
- **Identity source:** Orchestration identity
- **Audit path:** Via orchestration surface
- **Policy source:** Orchestration PDP
- **Direct exposure:** No direct entrypoint; ops tooling only
- **Adapter status:** Excluded — operational tooling only

---

### Tier 5: Plugin/Indirect Packages

These packages are reached only through already-regulated surfaces and do not need independent adapters.

#### `phlo-dlt`

- **Package:** `phlo-dlt`
- **Surface type:** Ingestion provider (plugin)
- **Enforcement mode:** N/A — reached through orchestration
- **Identity source:** Orchestration identity
- **Audit path:** Via orchestration surface
- **Policy source:** Orchestration PDP
- **Direct exposure:** No direct entrypoint; backend-only
- **Adapter status:** Excluded — plugin-only, no direct surface

#### `phlo-dbt`

- **Package:** `phlo-dbt`
- **Surface type:** Transformation provider (plugin)
- **Enforcement mode:** N/A — reached through orchestration
- **Identity source:** Orchestration identity
- **Audit path:** Via orchestration surface
- **Policy source:** Orchestration PDP
- **Direct exposure:** No direct entrypoint; backend-only
- **Adapter status:** Excluded — plugin-only, no direct surface

#### `phlo-transform`

- **Package:** `phlo-transform`
- **Surface type:** Transformation provider (plugin) + `phlo transform` CLI
- **Enforcement mode:** CLI mutation commands (`transform.run`, `transform.translate`, `transform.use`, `transform.install`) enforce the shared CLI surface when regulated mode is active; orchestrated runs use orchestration identity
- **Identity source:** Orchestration identity; CLI principal for direct commands
- **Audit path:** Via orchestration surface; CLI audit for direct commands
- **Policy source:** Orchestration PDP; CLI PDP for direct commands
- **Direct exposure:** `phlo transform run`, `plan`, `test`, `translate`, `use`, `install`
- **Adapter status:** `TransformSurfaceAdapter` declares mutation and read commands

#### `phlo-pandera`

- **Package:** `phlo-pandera`
- **Surface type:** Quality provider (plugin)
- **Enforcement mode:** N/A — reached through orchestration
- **Identity source:** Orchestration identity
- **Audit path:** Via orchestration surface
- **Policy source:** Orchestration PDP
- **Direct exposure:** No direct entrypoint; backend-only
- **Adapter status:** Excluded — plugin-only, no direct surface

#### `phlo-openmetadata`

- **Package:** `phlo-openmetadata`
- **Surface type:** Metadata catalog UI + API (external tool)
- **Enforcement mode:** N/A — external tool; not a Phlo-owned surface
- **Identity source:** External IdP
- **Audit path:** OpenMetadata native audit
- **Policy source:** OpenMetadata native RBAC
- **Direct exposure:** UI tools interact with it; not a Phlo direct surface
- **Adapter status:** Excluded — external surface, not Phlo-owned
- **gating.py entry:** `UNSUPPORTED_SERVICES` (`openmetadata`, `openmetadata-server`, `openmetadata-ingestion`)

#### `phlo-lineage`

- **Package:** `phlo-lineage`
- **Surface type:** Lineage plugin (no direct surface)
- **Enforcement mode:** N/A — plugin-only
- **Identity source:** Orchestration identity
- **Audit path:** Via orchestration surface
- **Policy source:** Orchestration PDP
- **Direct exposure:** No direct entrypoint
- **Adapter status:** Excluded — plugin-only

#### `phlo-alerting`

- **Package:** `phlo-alerting`
- **Surface type:** Alerting plugin (no direct surface)
- **Enforcement mode:** N/A — plugin-only
- **Identity source:** Orchestration identity
- **Audit path:** Via orchestration surface
- **Policy source:** Orchestration PDP
- **Direct exposure:** No direct entrypoint
- **Adapter status:** Excluded — plugin-only

#### Observability stack

- **Packages:** `phlo-otel`, `phlo-prometheus`, `phlo-grafana`, `phlo-loki`, `phlo-alloy`
- **Surface type:** Observability backends (infrastructure)
- **Enforcement mode:** N/A — infrastructure; not user-facing entrypoints
- **Identity source:** Internal service identity
- **Audit path:** Native observability backend audit
- **Policy source:** Deployment-level policy
- **Direct exposure:** Not direct user surfaces
- **Adapter status:** Excluded — infrastructure-only
- **gating.py entry:** `APPROVED_SERVICES` (`prometheus`, `grafana`, `loki`, `alloy`)

#### `phlo-traefik`

- **Package:** `phlo-traefik`
- **Surface type:** Reverse proxy / ingress (infrastructure)
- **Enforcement mode:** N/A — infrastructure
- **Identity source:** Internal service identity
- **Audit path:** Via ingress logs
- **Policy source:** Deployment-level policy
- **Direct exposure:** Not a direct user surface
- **Adapter status:** Excluded — infrastructure-only

#### `phlo-rustfs`

- **Package:** `phlo-rustfs`
- **Surface type:** FUSE filesystem driver (infrastructure)
- **Enforcement mode:** N/A — infrastructure
- **Identity source:** Internal service identity
- **Audit path:** Via host system
- **Policy source:** Deployment-level policy
- **Direct exposure:** Not a direct user surface
- **Adapter status:** Excluded — infrastructure-only

#### Table format resource providers

- **Packages:** `phlo-iceberg`, `phlo-delta`
- **Surface type:** Table format resource providers (plugins)
- **Enforcement mode:** N/A — reached through query engine
- **Identity source:** Query engine identity
- **Audit path:** Via query engine surface
- **Policy source:** Query engine PDP
- **Direct exposure:** No direct entrypoint; backend-only
- **Adapter status:** Excluded — resource provider plugins only

#### Observatory extension packages

- **Packages:** `phlo-observatory-example`
- **Surface type:** Observatory plugin/extension
- **Enforcement mode:** N/A — extensions inherit observatory's boundary
- **Identity source:** Via observatory
- **Audit path:** Via observatory
- **Policy source:** Via observatory
- **Direct exposure:** No independent direct entrypoint
- **Adapter status:** Excluded — extension-only

#### `phlo-testing`

- **Package:** `phlo-testing`
- **Surface type:** Test harness (no direct surface)
- **Enforcement mode:** N/A — test infrastructure
- **Identity source:** N/A
- **Audit path:** N/A
- **Policy source:** N/A
- **Direct exposure:** No direct entrypoint
- **Adapter status:** Excluded — test infrastructure only

#### `phlo-core-plugins`

- **Package:** `phlo-core-plugins`
- **Surface type:** Core plugin bundle (no direct surface)
- **Enforcement mode:** N/A — plugin bundle
- **Identity source:** Orchestration identity
- **Audit path:** Via orchestration surface
- **Policy source:** Orchestration PDP
- **Direct exposure:** No direct entrypoint
- **Adapter status:** Excluded — plugin bundle only

---

## Summary: Excluded Packages

These packages are explicitly excluded from needing a `regulated_surface` adapter and the reason why:

| Package | Reason excluded |
|---------|-----------------|
| `phlo-dlt` | Plugin-only; reached through orchestration |
| `phlo-dbt` | Plugin-only; reached through orchestration |
| `phlo-transform` | Plugin plus CLI; mutations gated by the transform surface adapter |
| `phlo-pandera` | Plugin-only; reached through orchestration |
| `phlo-lineage` | Plugin-only; no direct entrypoint |
| `phlo-alerting` | Plugin-only; no direct entrypoint |
| `phlo-clickstack` | Operational tooling; no direct entrypoint |
| `phlo-sling` | Operational tooling; no direct entrypoint |
| `phlo-traefik` | Infrastructure; not user-facing entrypoint |
| `phlo-rustfs` | Infrastructure; not user-facing entrypoint |
| `phlo-iceberg` | Resource provider; reached through query engine |
| `phlo-delta` | Resource provider; reached through query engine |
| `phlo-observatory-example` | Extension; inherits observatory boundary |
| `phlo-testing` | Test infrastructure only; no direct entrypoint |
| `phlo-core-plugins` | Plugin bundle only; no direct entrypoint |
| Observability backends | Infrastructure; not user-facing entrypoints |

---

## Validation Report Fields

Per-surface fields reported by `run_regulated_validation`:

| Field | Description |
|-------|-------------|
| `surface_type` | API, CLI, Web UI, backend, plugin |
| `enforcement_mode` | `regulated_surface` adapter, compiler-only, ingress-controlled, N/A |
| `identity_source` | Where the principal comes from |
| `audit_path` | How audit events are emitted |
| `policy_source` | Where authorization policy comes from |
| `direct_exposure` | Whether the surface is directly exposed to users/operators |

---

## Relationship to `phlo.security.gating`

The four frozensets in `gating.py` map to this inventory:

- **`UNSUPPORTED_SERVICES`**: Surfaces blocked in regulated mode (pgweb, openmetadata variants)
- **`PENDING_ADAPTER_SERVICES`**: Surfaces with adapters pending approval (none currently)
- **`INGRESS_OPTIONAL_SERVICES`**: Surfaces allowed only with ingress + upstream auth protection (hasura, postgrest, superset). Logs a warning but does not block.
- **`APPROVED_SERVICES`**: Surfaces fully allowed in regulated mode (phlo-api, dagster-*, cli, backends, observatory)

`INGRESS_OPTIONAL_SERVICES` surfaces log a warning in regulated mode but are not blocked.
They require a documented ingress boundary to be considered regulated-ready.

The inventory is the authoritative classification; `gating.py` is the runtime enforcement.
