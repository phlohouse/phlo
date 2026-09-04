# ADR 0049: V1 Continuity and Upgrade Contract

## Status

**Accepted**

- Date: 2026-09-01
- Decision owner: Phlo project maintainers
- Scope: the blessed v1 single-project, single-tenant production stack (PostgreSQL, MinIO, Nessie, Iceberg, Trino, Dagster)
- Supersedes: ad-hoc maintenance/recovery guidance across Dagster jobs, provider CLIs, and operations documentation

## Context

Phlo already has guarded table-maintenance contracts (plan token/revision), a strong CI recovery drill (SHA-256 verified PostgreSQL/Nessie/lake artifacts), and iceberg maintenance that refuses destructive orphan cleanup. But the operator path is fragmented across Dagster jobs, provider CLIs, and manual operations documentation. Backup and restore are not one neutral, versioned product contract, and existing migration commands do not upgrade a deployment.

This ADR freezes the continuity contract so Plans 010–013 can implement maintenance, backup, restore, and upgrade without inventing product or safety decisions. It changes no runtime code and promotes no support gate.

## Decision

### 1. Operator surface and shared state machine

The single supported surface is `phlo operations` with subcommands `maintenance`, `backup`, `restore`, and `upgrade`, each with `plan`, `apply`/`create`, `verify`, and `--format json` output. Rejected alternatives: Dagster-only (excludes CLI operators), per-provider ad-hoc CLIs (no unified journal), REST-only (no offline recovery).

Every operation produces versioned JSON plan and result envelopes (`schema_version = "1"`) with: operation ID, plan token, authorization action/resource, subject, target identity, expiry, and sanitized evidence. Plans expire; stale plans are rejected.

The durable journal is persisted in the run-evidence store (PostgreSQL in production, SQLite in development). The state machine is atomic and cross-process:

```
claimed → submitted → succeeded | failed | unknown
```

- **claimed**: the operation holds an exclusive claim (subject + action + target + plan token), with a claim expiry and takeover rule.
- **submitted**: the provider call was issued; the outcome is not yet known.
- **succeeded | failed**: the provider returned a definitive result.
- **unknown**: the process crashed or the response was lost after submission. `unknown` blocks automatic replay and blocks a new operation key until explicit reconciliation or an accepted abandonment rule is recorded.

Conflicting concurrent operations on the same target are rejected. A crash before submission releases the claim. A crash after submission leaves `unknown`. Reconciliation is explicit and operator-driven.

Fail-before-mutation: no destructive step runs until the plan is validated, the claim is held, and the journal records `submitted`.

### 2. Supported maintenance behavior

- **Inventory**: lists v1 tables with their provider, size, and snapshot state.
- **Compaction**: planned (dry-run returns a plan token/revision), then applied only against that exact unexpired plan. Provider-owned execution via the Iceberg/Trino maintenance executors.
- **Snapshot expiry**: same plan/apply pattern, with threshold validation.
- **Stale plans**: a plan whose revision no longer matches the current table state is rejected.
- **Orphan deletion**: unsupported. Orphan inventory may be diagnostic, but orphan deletion is excluded because the planned deletion set cannot be bound to execution. This is a permanent v1 exclusion, not a temporary limitation.

### 3. Backup-set consistency and ownership

The v1 state is owned by PostgreSQL (metadata + run evidence), MinIO (object storage: lake tables + Iceberg metadata), Nessie (catalog revisions), and Iceberg (table metadata + snapshots).

| Provider | v1 state | Backup artifact |
| --- | --- | --- |
| PostgreSQL | metadata DB + run-evidence DB | consistent pg_dump per database |
| MinIO | lake tables + Iceberg metadata objects | object listing + per-object checksums |
| Nessie | catalog revisions | Nessie export (branch/content) |
| Iceberg | table metadata + snapshots | covered by MinIO object backup (metadata files) |

Backup-set rules:

- **Quiescing**: writes are quiesced (Dagster runs paused) before the backup begins; quiescing is confirmed before any provider artifact is captured.
- **Provider order**: PostgreSQL → Nessie → MinIO (metadata before data blobs).
- **Immutable set ID**: the backup set has a unique `set_id` (UUID) and records the source deployment ID, a version inventory (package versions, image digests), and per-artifact SHA-256 checksums.
- **Atomic finalization**: the set manifest is written last; a set without a complete manifest is always unusable.
- **Completeness**: every declared provider artifact must be present and checksum-verified; a partial set is always unusable.
- **Verification**: `phlo operations backup verify --backup-set <set_id> --format json` independently verifies identity, membership, versions, and checksums without mutating any service.
- **Retention**: ownership is the operator's; Phlo does not auto-delete backup sets.
- **Failure cleanup**: a failed backup attempt removes its partial artifacts and records `failed` in the journal.

### 4. Restore and post-restore reconciliation

Restore targets are **explicitly named and confirmed**. An implicit "current" or in-place target is forbidden.

- **Target types**: a new, empty deployment (fresh project dir + clean services) or an explicitly identified existing deployment. Source ≠ target.
- **Plan-bound confirmation**: `restore plan --backup-set <set> --target <target-id>` is mutation-free; `restore apply --plan <path> --confirmation-token <token>` restores only the bound target.
- **Validation before mutation**: the backup set is verified (checksums, completeness) before any destructive step. Corrupt set, source-as-target, target mismatch, stale token, authorization failure, or preflight failure → zero restore calls.
- **Restore order**: reverse of backup order (MinIO → Nessie → PostgreSQL).
- **Partial failure**: a failed restore records the failing provider and the completed providers; resume/repair is a new plan against the same target, not an automatic continuation.
- **Post-restore reconciliation**: catalog revision check, object checksum verification, a final query assertion, and Plan 008 run-evidence availability check.

### 5. Supported upgrade pair and recovery boundary

The supported upgrade pair is **one declared previous-to-candidate version transition**, materialized immutably (pinned package versions + image digests). Compatibility/migration registry ownership is the core capability layer.

- **Mandatory pre-upgrade verified backup**: the upgrade plan requires a verified backup set before any upgrade step.
- **Provider order**: PostgreSQL migrations → Nessie/Iceberg metadata migrations → MinIO policy updates.
- **Pre/post checks**: pre-upgrade (current version matches, backup verified, no in-flight operations); post-upgrade (schema migration complete, evidence available, query assertion).
- **Rollback-safe point**: the last step before the first irreversible schema migration. Fault before this point → restore from backup. Fault after → bounded forward repair (declared migration completion steps, not a full re-run).
- **Existing `phlo migrate` and `phlo config upgrade`**: explicitly not deployment-upgrade acceptance. They remain schema/config migration tools.

### 6. Evidence requirements

Required artifacts per operation:

| Operation | Required evidence |
| --- | --- |
| maintenance plan/apply | plan token, revision, threshold, provider result, journal entry |
| backup create/verify | set manifest (set_id, source deployment, version inventory), per-artifact checksums, verification result |
| restore plan/apply | plan token, confirmation token, target identity, per-provider restore result, post-restore reconciliation result |
| upgrade plan/apply | previous/candidate version pair, backup set id, pre/post check results, migration journal |

No RTO/RPO is claimed. The operations guide's explicit statement (no published RTO/RPO commitment) is preserved unless a separately approved policy and representative measurement evidence exist.

## Consequences

- Plans 010–013 implement against one contract: plan 010 (maintenance), plan 011 (backup), plan 012 (restore), plan 013 (upgrade). No invented product or safety decisions.
- Orphan deletion is excluded for v1.
- The `phlo operations` surface is the single continuity entry point for CLI and API.
- Every destructive operation is plan-bound, confirmed, journaled, and fail-before-mutation.

## Alternatives Considered

- **Dagster-only maintenance**: rejected. Excludes CLI operators and couples continuity to one orchestrator.
- **Per-provider backup CLIs without a set manifest**: rejected. No cross-provider consistency or atomic finalization.
- **Implicit in-place restore**: rejected. Source must ≠ target; confirmation must be explicit.
- **Automatic orphan deletion**: rejected. The planned deletion set cannot be bound to execution.
- **RTO/RPO publication without evidence**: rejected. Observed durations are not objectives.

## Related

- ADR 0047: V1 Production Trust and Readiness Contract
- ADR 0048: Blessed Run-Evidence Composition
- Plans 010–013: implementation slices
- `scripts/recovery_drill.py`: the CI recovery drill (precursor to plan 011)
- `src/phlo/capabilities/maintenance.py`: the existing plan/execute contract
