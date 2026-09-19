# Run in production

This guide turns a reviewed Phlo project into a supported Compose deployment with protected secrets, durable volumes, verified backups, and an explicit release boundary.

## Before you start

- You have a reviewed version tag, a deployment host with Docker Compose, and an operator responsible for backups and upgrades.
- You have replaced development credentials and configured the production environment in `.phlo/.env.local`.
- You have installed the release artifacts that the bundled support contract expects.

## 1. Check the support tier

Check the bundled, offline support contract before selecting artifacts for production:

```bash
phlo support status
```

The command reads the bundled contract without contacting a registry. A real run prints `Manifest: bundled (trusted)`, a `Compatible` result, a `Production ready` result, package-by-package expected and installed versions, and the release gates. The current development environment reported `Compatible: False` and `Production ready: False` because it contained unexpected optional packages. Maintainers can review the full [support registry](https://github.com/iamgp/phlo/blob/main/registry/support/v1.json).

The tier describes the support contract for the package. It does not promise high availability, multi-region operation, or an operator's backup retention.

## 2. Render the Compose deployment

Initialize the generated infrastructure from the installed providers and set production mode:

```bash
phlo services init
export PHLO_ENVIRONMENT=production
phlo services preflight --production --json --output .phlo/preflight.json
```

Phlo renders the core Compose shape around Dagster, PostgreSQL, MinIO, Nessie, and Trino when the default stack is selected. Optional API, Observatory, and BI services are added only when enabled.

## 3. Protect secrets and volumes

Keep credentials in `.phlo/.env.local` and retain the named volumes used by stateful services:

```bash
chmod 600 .phlo/.env.local
phlo services start
phlo services ports
```

PostgreSQL stores orchestration state, MinIO stores object data, Nessie stores catalog state, and the generated `.phlo/volumes/` paths identify local volume mounts. Back up these providers together so catalog metadata and objects remain consistent.

## 4. Create and verify a backup

Use the plan-first backup commands instead of copying one service volume in isolation:

```bash
phlo operations backup create --target /backups/phlo-2025-01-15
phlo operations backup verify --backup-set /backups/phlo-2025-01-15
```

Creation finalizes one manifest after provider artifacts and SHA-256 digests succeed. Verification is read-only and rejects partial, corrupt, mixed-run, or wrong-owner sets.

## 5. Upgrade with the supported operation

Inspect the operations help and use the bound upgrade flow when changing deployment versions:

```bash
phlo operations --help
phlo operations upgrade plan --from 0.14.0 --to 0.15.0 --backup-set /backups/phlo-2025-01-15 --target /srv/phlo
phlo operations upgrade apply --plan .phlo/upgrade-plan.json --confirmation-token <token>
```

The verified operations flow requires a backup of the exact source state and binds the plan to source, candidate, backup digest, migration digest, and target. `phlo migrate` and `phlo config upgrade` are configuration migrations, not deployment-upgrade acceptance.

## 6. Monitor the deployment

Keep health, service status, logs, catalog history, and preflight evidence in the operator runbook:

```bash
phlo doctor
phlo status
phlo services status
phlo logs --lines 200 --timestamps
phlo metrics
```

Monitor Dagster runs and checks, PostgreSQL and MinIO storage, Nessie catalog health, Trino query failures, API authorization, and the age of `.phlo/logs/` evidence.

## Verify

```bash
phlo services preflight --production --json
phlo doctor
```

Preflight returns `"passed": true` only when required production checks pass, and doctor reports no live service failures. Retain both outputs with the deployment record.

## Release artifact support boundary

The release workflow prepares versioned Python packages, updates bounded package compatibility and checked support-manifest references, refreshes the lockfile, and publishes packages from the merged release commit. The core-service image workflow builds images for `phlo-api` and Observatory when a GitHub Release is published.

Publishing an artifact and pulling an image proves that the registry contains the requested object. It does not establish support for every image tag or digest. The v1 support boundary also excludes high-availability and multi-region deployment guarantees. A release artifact remains subject to the package tier, the support manifest, the exact deployment configuration, and the operator's production checks.

## Related

- [Secure the stack](secure-the-stack.md) for authorization and secret handling.
- [Monitor and debug](monitor-and-debug.md) for runtime diagnosis.
- [Packages](../reference/packages.md) for package support tiers.
