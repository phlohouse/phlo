# Upgrade Phlo

Phlo has separate commands for package changes, project configuration, data migrations, and source codemods. The guarded deployment-upgrade command currently proves one fixture transition only. It does not upgrade a live deployment.

## Supported boundary

The only accepted pair is `0.14.0` to `0.15.0`. This pair is an immutable fixture policy used to test the continuity journey; it is not a declaration that a real `0.14.0` deployment can be upgraded in place. Every other pair fails before mutation. Current package version `0.16.2` does not add another supported pair.

The boundary is defined in [`phlo.operations.upgrade`](../../src/phlo/operations/upgrade.py) and tested in the [upgrade contract](../../tests/operations/test_upgrade_contract.py).

## Prepare the package set

1. Record the installed package set and retain the current lock file.

   ```bash
   uv pip freeze > phlo-packages.before.txt
   cp uv.lock uv.lock.before
   ```

2. Change the Phlo package constraints in `pyproject.toml` as one reviewed set. The root extras define the intended bundles: `defaults` contains the supported default stack, `core-services` contains the service core, and `runtime` contains runtime libraries. `observe` and `openmetadata` are opt-in. Do not derive support from installability; check [`registry/support/v1.json`](../../registry/support/v1.json).

3. Resolve and install through the project's normal locked workflow.

   ```bash
   uv lock
   uv sync --locked
   ```

Keep `uv.lock.before`, the old environment or images, and the pre-upgrade data backup until all verification passes. Updating one Phlo package independently can leave entry points, service images, and provider contracts on different versions.

## Migrate project configuration

Preview the current configuration migration:

```bash
phlo config upgrade --plan-only
```

`phlo config upgrade` has one step: add the default `infrastructure` section to `phlo.yaml`. If that section exists, the command refuses unless `--force` replaces it. The command validates a temporary candidate and atomically replaces `phlo.yaml` only after validation.

```bash
cp phlo.yaml phlo.yaml.before
phlo config upgrade
phlo config validate
```

Review before using `--force`; it discards the existing infrastructure section.

## Migrate data and code

Data migration specifications are independent of the deployment fixture command. Validate and dry-run each reviewed spec before authorising a write:

```bash
phlo migrate list
phlo migrate validate <migration.yaml>
phlo migrate run <migration.yaml> --dry-run
phlo migrate run <migration.yaml>
phlo migrate status
```

The dated decorator codemod reports changes by default. Gate or inspect it, then write:

```bash
phlo migrate decorators-2026-05 . --check
phlo migrate decorators-2026-05 . --diff
phlo migrate decorators-2026-05 . --write
```

The write forms require mutation authorisation. The migration CLI contract is in [`migrate.py`](../../src/phlo/cli/commands/migrate.py).

## Exercise the fixture upgrade contract

This procedure applies only to the supported fixture pair.

1. Create and verify a backup whose manifest records Phlo `0.14.0`.

   ```bash
   phlo operations backup create --target <new-empty-backup-root>
   phlo operations backup verify --backup-set <backup-set-directory>
   ```

2. Create a plan and save its JSON output. The target must be new or empty.

   ```bash
   phlo operations upgrade plan \
     --from 0.14.0 --to 0.15.0 \
     --backup-set <backup-set-directory> \
     --target <new-empty-fixture-target> \
     --format json > upgrade-plan.json
   ```

3. Configure `PHLO_OPERATIONS_JOURNAL_DIR`, obtain mutation authorisation, then apply with the token from the plan.

   ```bash
   phlo operations upgrade apply \
     --plan upgrade-plan.json \
     --confirmation-token <plan-token> \
     --fixture-substrate \
     --format json
   ```

Apply writes version markers beneath the fixture target in this order: `postgres.schema`, `nessie.catalog`, `iceberg.metadata`, then `minio.policy`. It does not change packages, containers, schemas, catalogues, object-store policy, or a running service.

## Verify and choose recovery action

Require all of these before discarding the old package set or backup:

- `uv sync --locked` completes from the committed lock;
- `phlo config validate` succeeds;
- applicable migration dry-runs and tests pass;
- `phlo services status` reports the selected services healthy after a real package deployment;
- the fixture result has `state: succeeded`, `accepted: true`, and successful reconciliation for all four steps.

`postgres.schema` is the last declared rollback-safe step. A failure in that step returns `rollback_action: restore`. Once `nessie.catalog` starts, the contract does not claim rollback safety; failures return bounded `forward_repair` details. If reconciliation throws after submissions, the result is `unknown`, automatic replay is blocked, and an operator must reconcile manually.

For a real package rollout, the rollback-safe point is before data or provider migrations begin: retain the old lock/images and a verified backup, and verify that backup while the old package set can still run. The current CLI cannot restore a live deployment, so do not call the fixture `rollback_action` an operational rollback plan.
