# ADR 0061: Run-evidence store on SQLite in development and PostgreSQL in production

## Status

**Accepted (back-filled 2026-10-30)**

- Original decision: July 2026, migration rule August 2026
- Evidence: [#577](https://github.com/phlohouse/phlo/pull/577) (merged 2026-07-13,
  durable provider-neutral evidence store); issue
  [#726](https://github.com/phlohouse/phlo/issues/726) and
  [#737](https://github.com/phlohouse/phlo/pull/737) (merged 2026-08-21,
  explicit per-backend migrations)
- Decision owner: Phlo project maintainers

## Context

[ADR 0048](0048-blessed-run-evidence-composition.md) and
[ADR 0049](0049-v1-continuity-and-upgrade-contract.md) depend on a durable
run-evidence store and state in passing that it uses PostgreSQL in production and
SQLite in development. Neither records that choice, its storage rules or its
migration policy.

#577 introduced the store with versioned provider-neutral records for runs,
events, stages, resources, lineage edges, quality results, catalogue changes and
artifacts. It needed to work in a laptop project with no database service and in
a production deployment with several processes.

The first implementation created the SQLite schema by rewriting PostgreSQL DDL
with string replacements, mixed a Python reconciliation step with separate SQLite
scripts, and ignored duplicate-column errors. Issue #726 recorded that this could
hide drift between backends and risk data loss on upgrade or restore.

## Decision

1. **One SQL implementation, two adapters.** `_SqlRunEvidenceStore` implements
   the contract once. `SQLiteRunEvidenceStore` serves single-process local
   development and tests. `PostgresRunEvidenceStore` serves production and
   requires the `runtime` extra for `psycopg2`.
2. **Selection fails closed outside development.** `default_run_evidence_store()`
   uses `PHLO_RUN_EVIDENCE_DB_URL` when it is set. Without it, `prod`,
   `production`, `staging` and `regulated` environments raise an error instead of
   falling back to SQLite. Development uses `PHLO_RUN_EVIDENCE_SQLITE_PATH`,
   defaulting to `.phlo/run-evidence.sqlite`.
3. **Append is transactional, idempotent and tenant-scoped.** Producer and event
   identities and normalised record identifiers are scoped by `project_id` through
   composite keys on both backends. Replaying an event with the same identity and
   content is a no-op; reusing an identity with different content raises
   `IdempotencyConflict`.
4. **Payloads are redacted before storage.** Checksums are calculated over the
   redacted canonical representation, so tamper evidence never requires storing
   secrets.
5. **Explicit, versioned migrations per backend.** Each schema version has a
   PostgreSQL file and a SQLite file under one shared, monotonic version number.
   Applied migrations and their checksums are recorded in the same transaction.
   Unknown newer versions and checksum drift fail closed. No DDL is translated
   between dialects (#737).
6. **The schema version is part of the release set.** The support manifest pins
   the database schema version (currently 5, from `phlo/run_evidence/models.py`).

## Consequences

- Local projects work without a PostgreSQL service, and production cannot silently
  write evidence to a local file.
- Every schema change adds two migration files and must keep the backends
  logically equivalent. Tests cover upgrades from earlier schema versions and
  checksum drift.
- PostgreSQL pool settings are parsed only when the first PostgreSQL pool is
  created, so invalid pool settings do not break SQLite or injected connections.
- Run-evidence names such as `PipelineRun` are part of the versioned schema and
  are retained until a future schema version migrates them
  ([public name compatibility](../../reference/public-name-compatibility.md)).
- The continuity journal in ADR 0049 uses the same backend rule.

## Alternatives Considered

- **PostgreSQL only:** not adopted; local development and tests would need a
  database service.
- **SQLite in production:** rejected; the selector raises an error instead.
- **Generating SQLite DDL from PostgreSQL DDL:** the original approach, replaced
  in #737 because text substitution and ignored errors could hide drift.
- **Dropping local SQLite:** listed as a non-goal in #726.

## Related

- [ADR 0047](0047-v1-production-trust-and-readiness.md): durable evidence in the production contract.
- [ADR 0048](0048-blessed-run-evidence-composition.md): what the store records for the blessed pipeline.
- [ADR 0049](0049-v1-continuity-and-upgrade-contract.md): continuity journal and upgrade rules.
- `src/phlo/run_evidence/store.py` and `src/phlo/sql/`.
