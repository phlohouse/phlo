# Prepare a Compose deployment for production

This guide prepares a reviewed Phlo project for a single-host Compose deployment. It checks the support contract, protects credentials, starts the generated stack, and records readiness evidence.

This deployment model does not provide high availability, multi-region operation, or a built-in live restore or upgrade operation.

## Before you start

- You have a reviewed version tag and the exact package versions required by `registry/support/v1.json`.
- The deployment host runs Docker Compose and has enough durable storage for the generated volumes.
- An operator owns TLS termination, network exposure, credentials, monitoring, backups, restore testing, and upgrades.
- The production credential values are ready for installation after the stack is rendered.

## 1. Check the support contract

```bash
phlo support status
```

The command reads the bundled contract without contacting a registry. Confirm that `Compatible` and `Production ready` are both `True`, then review every package version and release gate in the output.

A package tier does not promise high availability, multi-region operation, or a working operator restore procedure.

## 2. Render and inspect the deployment

```bash
phlo services init
export PHLO_ENVIRONMENT=production
phlo services preflight --production --json --output .phlo/preflight.json
```

Phlo renders the selected services into `.phlo/docker-compose.yml`. Review the images, published ports, volume mounts, health checks, and environment files before starting the stack.

Do not expose PostgreSQL, MinIO, Nessie, Trino, Dagster, the API, or Observatory to an untrusted network without appropriate authentication, authorisation, TLS, and network controls.

## 3. Protect credentials and state

Replace every development credential in `.phlo/secrets/.env`, then restrict the file before starting services:

```bash
chmod 600 .phlo/secrets/.env
phlo services start
phlo services ports
```

Use `phlo services ports` to confirm the actual host exposure. Keep the stateful service volumes on durable storage and include their capacity in monitoring.

### Configure API operation controls before adding workers

Without shared controls, run exactly one API replica with `WEB_CONCURRENCY=1`.
Local development retains SQLite idempotency, filesystem exclusion, and a bounded
in-memory rate limiter. Startup warns when multiple workers use local controls.
The warning does not discover replicas or make local storage safe to share.

To coordinate API workers and replicas, set these values in the API's protected
environment, then render and inspect the Compose configuration again:

```bash
PHLO_API_OPERATION_CONTROLS_DB_URL='postgresql://<user>:<password>@postgres:5432/<database>'
PHLO_API_OPERATION_CONTROLS_NAMESPACE='my-project'
```

Use the same database and namespace on every replica, regardless of its local
project path. Use different namespaces for unrelated projects. The DSN can point
at the same database as `PHLO_RUN_EVIDENCE_DB_URL`; configuring run evidence alone
does not enable shared operation controls. API startup checks connectivity and
creates the `phlo_api_*` control tables. Grant the API role schema creation rights
for first startup, or initialise the tables with the same provider before removing
those rights. A configured but unavailable store fails closed, never back to SQLite.

Before switching from SQLite or changing the namespace, stop mutation traffic and
drain every old worker. Retain the old journal and resolve every pending or unknown
operation against provider evidence. There is no automatic journal migration.
An unresolved operation must not be retried in an empty database or namespace.
Back up the shared controls with PostgreSQL and test their restore alongside the
provider state. Do not delete pending or unknown rows to clear a conflict.

With shared controls, job, run, asset-launch, and branch actions no longer require
the single-replica/process assertions. They still require
`PHLO_V1_ACTIONS_REF_TAG_CONTRACT=1`, authorisation, confirmation, expected provider
state, and the existing signature checks where applicable. Claims exclude the
resource independently of actor and request digest. Completed responses replay
for 24 hours. Pending and unknown outcomes block a new key until an operator
records verified success, failure, or `safe_to_retry` evidence through the existing
resolution service. Drain the original worker before resolving an in-flight claim.

Both rate-limit backends admit at most 4,096 live principal/operation buckets.
Idle buckets expire after 60 seconds. At capacity, a new principal receives 429
instead of evicting a live bucket and resetting another principal's allowance.
PostgreSQL uses its own clock and transaction advisory locks for atomic admission.
Short control transactions serialize per namespace; provider calls do not hold
that transaction lock. Session advisory locks also coordinate workflow apply.

Shared controls do not replicate project files. Workflow proposals, integrity
keys, application receipts, and generated files must use the same durable project
volume on every writer; otherwise keep one workflow writer. Query workspaces,
audit proposals, Git publishing, and staging code promotion retain their existing
single-replica/process gates because their state remains file-backed. Do not set
those assertions for a multi-replica deployment. Continuity API journals use the
shared store when configured; the CLI still uses its configured file journal.
Do not run CLI and API continuity mutations concurrently against the same target.
Existing PostgreSQL identity and incident stores continue to enforce signature
consumption, version checks, and signed resolution. The controls database does
not replace those contracts or make fixture-only restore/upgrade production-ready.

Durable API audit appends intentionally remain synchronous. Every append waits
for the filesystem lock, flush, and `fsync`; rotation also synchronises directory
metadata. This adds storage latency to the response and occupies a worker thread.
The async replay helper offloads these writes from the event loop and waits for
durability before returning success. Direct synchronous callers still pay that
latency. Preserve and collect each replica's audit files, monitor their disk and
write latency, and size worker capacity accordingly. A failed audit after a
provider effect reports `mutation_succeeded_audit_failed` and blocks automatic
re-execution. This implementation chooses the issue's documented synchronous
audit option, not an asynchronous queue or a cross-replica audit archive.

To verify shared control guarantees on a disposable database, with Docker running:

```bash
uv run --locked pytest packages/phlo-api/tests/test_operation_controls_postgres.py -m integration -q
uv run --locked pytest packages/phlo-api/tests/test_operation_controls.py -q
```

## 4. Create and verify a backup

Follow [Create and verify a backup](create-and-verify-a-backup.md). Retain the accepted manifest and verification result outside the deployment host.

The current Phlo CLI cannot restore a live deployment from that backup. Before go-live, test a provider-specific restore of PostgreSQL, object storage, the catalog, and every other stateful provider in an isolated environment.

## 5. Define restart, restore, and upgrade procedures

Before accepting traffic, document and test:

- how the stack starts after a host restart;
- how operators restore each stateful provider to a consistent point;
- how operators roll back a failed deployment change;
- how package and image versions move between releases;
- when an operator must stop and escalate.

Do not use `phlo operations restore apply` or `phlo operations upgrade apply` for live operations. Both commands require `--fixture-substrate` and only stage fixture artifacts.

## 6. Monitor the deployment

```bash
phlo doctor
phlo status
phlo services status
phlo logs --lines 200 --timestamps
phlo metrics summary
```

Monitor Dagster runs and checks, storage capacity, catalog health, query failures, API authorisation failures, and the age of backup and evidence artifacts.

## Verify readiness

```bash
phlo services preflight --production --json
phlo doctor
phlo services status
```

Do not accept traffic until preflight returns `"passed": true`, doctor reports no live service failures, every required service is healthy, and the operator has completed a restore test.

Retain the preflight output, package versions, rendered Compose file, backup verification, restore-test result, and approval with the deployment record.

## Release artifact support boundary

The release workflow publishes versioned Python packages and builds images for `phlo-api` and Observatory when a GitHub Release is published. The support manifest records the accepted package set and compatibility boundary.

The presence of an artifact in a registry does not make every tag, digest, or deployment configuration supported. Check the package tier, support manifest, deployment configuration, and production checks together.

## Related

- [Secure the stack](secure-the-stack.md) for authorisation and secret handling.
- [Maintain and recover a deployment](maintain-and-recover.md) for supported runbooks and current recovery limits.
- [Monitor and debug](monitor-and-debug.md) for runtime diagnosis.
- [Packages](../reference/packages.md) for package support tiers.
