# Governance and datasets

Governance metadata makes ownership, consumers, service expectations, access, and publication state visible alongside your data. After reading this page, you can connect declarations, contract checks, and Dataset transitions into a controlled publication workflow.

## What Phlo governs

The governance surface merges `@phlo.contract`, `@phlo.publish`, `@phlo.access`, and `@phlo.observe` declarations. The resulting metadata can include an owner, lifecycle, PII marker, audience, consumers, SLA, access policies, classifications, observability, and warnings.

`Consumer` carries a name, contact, and usage. `SLA` carries freshness hours, a quality threshold, a maximum failure count, and notification targets.

## Contracts and the schema registry

Phlo stores immutable normalised schema snapshots in PostgreSQL. Registry lookup accepts `PHLO_REGISTRY_DB_URL`, `PHLO_LINEAGE_DB_URL`, or `DAGSTER_PG_DB_CONNECTION_STRING`.

Use `phlo contracts snapshot` to store a snapshot and `phlo contracts check` to compare a table with its previous snapshot. Normal materialisation refreshes contracts unless you pass `--no-contract-refresh` to `phlo materialize`. Use `phlo schema-migrate` when the compatibility result requires a planned schema change.

## Governance readiness

`phlo governance check` validates governed tables for publish and production readiness and exits with status 1 when a check fails. The underlying surface reports warnings for missing declarations, ownership, classification, and other inconsistent metadata.

`phlo governance export` writes the browser-safe governance read model. Use `--module` to import a workflow module or Python file that registers declarations, and use `--json` for machine-readable output.

## Dataset identity and state

Dataset IDs use `candidate:<table_id>` for candidates and `<table_id>` for promoted datasets. Candidate workflow states are `claimed`, `review`, `promoted`, and `rejected`. Publication states are `draft`, `published`, and `retired`.

Use `phlo dataset list` to list canonical projections and `phlo dataset show <dataset-id>` to inspect one projection. `phlo dataset transition <dataset-id> <action>` applies `claim`, `review`, `promote`, `reject`, `publish`, or `retire` through the Dataset state store.

The durable store is provider-owned and is selected by default. The explicit `memory` mode is process-local test state. Set `PHLO_DATASET_STATE_STORE=memory` only for local experiments or tests, or install `phlo-postgres` for the durable dataset state capability.

`phlo dataset migrate-overlay plan` reads the legacy Observatory overlay without changing a store. The `apply` and `discard` commands are audited mutation paths, and `apply` uses the planned source digest and idempotency contract.

## Access declarations and RBAC

Access declarations become governance read-model policies with roles, PII columns, a policy name, and metadata. The enforcement path and bearer-token scopes are documented in [Auth and access](../reference/auth-and-access.md).

Use [Secure the stack](../guides/secure-the-stack.md) when you need to configure route guards, tenant context, OAuth2 Proxy, or RBAC synchronisation.

## Where metadata appears

The governance export feeds browser-safe views, while API and MCP integrations expose read models to clients. Observatory shows asset metadata, quality evidence, lineage, and run reports through its configured service.

## Where to look next

Use [Govern a dataset](../guides/govern-a-dataset.md) for a task sequence and [Configuration](../reference/configuration.md#tenant-scope) for tenant scope.
