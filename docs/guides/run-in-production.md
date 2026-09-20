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
