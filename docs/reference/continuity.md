# Continuity contract

Phlo can create and verify a backup set for the frozen v1 state owners. Restore and deployment upgrade apply are not live operational procedures: the current provider adapters write files and version markers beneath an empty fixture target.

## Backup contributors and captured state

Every set must contain all four contributors in this exact order. Missing, additional, duplicate, or reordered contributors prevent finalisation or verification.

| Order | Contributor | Captured state | Explicit limitation |
| --- | --- | --- | --- |
| 1 | PostgreSQL | A gzip-compressed `pg_dump` of the configured database | Captures the selected database, not every database, PostgreSQL cluster configuration, roles, or external secret material |
| 2 | Nessie | Sorted branch names and commit hashes in `catalog.json` | This is revision inventory, not a full Nessie repository/database export |
| 3 | MinIO | Every object in every discovered non-system bucket, plus `objects.json` with size and SHA-256 per object | Excludes buckets named `minio` and `minioadmin`; does not capture users, policies, lifecycle rules, encryption keys, or server configuration |
| 4 | Iceberg | Table name, current snapshot ID, record count, and byte count in `inventory.json` | This is reconciliation inventory. MinIO captures the Iceberg metadata and data files themselves |

The contributor implementations are [PostgreSQL](../../packages/phlo-postgres/src/phlo_postgres/continuity.py), [Nessie](../../packages/phlo-nessie/src/phlo_nessie/continuity.py), [MinIO](../../packages/phlo-minio/src/phlo_minio/continuity.py), and [Iceberg](../../packages/phlo-iceberg/src/phlo_iceberg/continuity.py). Dagster run storage and Observatory settings are covered only to the extent that they reside in the dumped PostgreSQL database. Bind-mounted project code, configuration, logs, local operation journals, credentials, external identity systems, optional services, and state outside these four contributors are excluded.

## Consistency and quiesce evidence

The coordinator calls a quiesce hook before writing artifacts and records its returned mapping in the manifest. The CLI currently uses the default hook, which records `{"quiesced": true, "strategy": "coordinator-default"}` but does not stop services, pause Dagster, lock writers, or establish a cross-system snapshot. `pg_dump` is internally consistent for PostgreSQL, but the four contributors run sequentially. Objects, Nessie refs, and Iceberg inventory can therefore describe different instants if writes continue.

For a recovery-grade set, operators must establish and retain their own write-quiescence procedure before invoking the CLI. Treat the manifest's current default quiesce field as coordinator evidence, not proof that producers were stopped.

## Creation and verification

`phlo operations backup create --target <new-empty-directory>` requires mutation authorisation and `PHLO_OPERATIONS_JOURNAL_DIR`. The target must be new or empty. Core claims the durable operation journal before mutation, writes each contributor under its owned prefix, recomputes file sizes and SHA-256 digests, and writes `manifest.json` atomically only after every contributor succeeds. A failed staging directory has no valid final manifest and is unusable.

`phlo operations backup verify --backup-set <set-directory>` is read-only. Add `--expected-deployment <deployment-id>` to bind source ownership. Verification checks:

- manifest schema version `1`, completeness, source deployment, and a recorded `phlo` version;
- exact contributor membership and one operation ID for the run;
- contained, non-symlink artifact paths;
- every declared file's presence, size, and SHA-256;
- absence of undeclared files.

Stable failure reasons are defined by [`BackupVerificationReason`](../../src/phlo/capabilities/continuity.py). Verification proves the set's structure and bytes. It does not connect to source services, replay the backup, or prove application-level recoverability.

## Restore planning, order, and limitation

Planning requires a verified set and an explicit target that is new or empty, is not the source set, and is neither the filesystem root nor the user's home directory. The four-hour plan binds the set digest, target, and token. Apply reverifies all of them, requires mutation authorisation, a durable journal, the confirmation token, and the explicit `--fixture-substrate` acknowledgement.

Apply order is the reverse authority order:

1. Iceberg inventory;
2. MinIO objects;
3. Nessie branch inventory;
4. PostgreSQL dump.

After all steps, each owner reconciles its staged files against backup evidence. The command succeeds only if every reconciliation passes. It does **not** import SQL into PostgreSQL, upload objects to MinIO, recreate Nessie references, or rebuild an Iceberg catalogue. PostgreSQL restore produces `postgres/restored.sql`; the other adapters copy their backup artifacts under the target. Use provider-native, separately tested procedures for a live restore.

The coordinator and ordering are implemented in [`backup.py`](../../src/phlo/operations/backup.py) and [`restore.py`](../../src/phlo/operations/restore.py). Their safeguards are exercised by the [backup](../../tests/operations/test_backup_set.py) and [restore](../../tests/operations/test_restore.py) contract tests.
