# ADR 0048: Blessed Run-Evidence Composition

## Status

**Accepted**

- Date: 2026-08-31
- Decision owner: Phlo project maintainers
- Scope: the blessed v1 DLT → dbt → Pandera → Iceberg → Nessie/WAP pipeline
- Supersedes: ad-hoc per-run evidence interpretation in WAP and the reconciler

## Context

The reconciler correctly evaluates explicit durable records, but the blessed
WAP evidence profile is nearly empty (`RequiredEvidenceProfile(profile_id="wap",
version="1", provider="dagster")`), evidence emission is intentionally
observational and swallows sink failure after provider work, and there is no
provider-contribution capability family. The result: a run can complete with
almost no required evidence, sink outage is invisible, and promotion semantics
are implied rather than decided.

This ADR freezes the contract so Plans 007–008 can implement profile composition
and provider contributions without inventing precedence, duplicate-handling, or
promotion rules. It changes no runtime code and promotes no support gate.

## Decision

### 1. Evidence fact ownership

Each fact has exactly one authoritative owner and one observation point. A
provider never imports another provider and never synthesizes a fact from table
or catalog existence.

| Owner | Facts it owns | Observation point |
| --- | --- | --- |
| DLT | ingest start/end, resource in/out, rows loaded, input resource identity | DLT pipeline run events, emitted to the durable store |
| dbt | transform start/end, model artifacts, selected/compiled manifest identity | dbt run/asset events emitted to the durable store |
| Pandera | quality check outcomes, per-check severity/blocking, evaluated/failed counts | Pandera check evaluation events emitted to the durable store |
| Iceberg | snapshot created, snapshot id, table identity | Iceberg snapshot-created event emitted to the durable store |
| Nessie | catalog transition: branch, commit hash, before/after state | Nessie commit/catalog event emitted to the durable store |
| Dagster/WAP | run terminal state, attempt identity, promotion decision, failure reasons | Dagster run/attempt events + WAP promotion event emitted to the durable store |
| Reconciler | completeness evaluation, missing/abandoned classification, profile digest | read-only reconciliation over the durable store |

Canonical project/run/attempt/stage/resource identities are `project_id`,
`run_id`, `attempt`, `stage`, and the resource reference each owner declares.
The authoritative moment is the owner's own emission, not a downstream observer's
deduction.

### 2. Deterministic profile composition

The blessed profile has a **root required-contributor manifest of six stable
IDs** — one each for DLT ingest, dbt transform/artifacts, Pandera quality,
Iceberg snapshot, Nessie catalog transition, and Dagster/WAP terminal/promotion.
Ownership of changes to that set: the core capability layer owns the family and
the manifest; a version/digest bump requires a decision, never silent mutation.

Composition rules:

- Each provider contributes a declarative contribution: stable `contribution_id`,
  `profile_id`/`version` it satisfies, required stages/fields/record families,
  and declared dependencies. Contributions register through the neutral
  capability registry; discovery order must not affect the result.
- Composition first compares **discovered contribution IDs against the root
  required set** before considering provider-declared dependencies. A wholly
  absent provider produces `unavailable`/`incomplete`, never an empty or
  partially healthy profile.
- Duplicate contribution IDs are rejected. Conflicting contributions for one
  profile/version are rejected. Contribution cycles are rejected.
- The canonical digest includes the sorted required set and the sorted
  discovered set, so composition is deterministic and input-bound.

### 3. Terminal and promotion semantics

Evidence completeness and run success/promotion are **separate dimensions**.
Only a successful, complete attempt may promote.

| Outcome | Evidence complete | Run successful | Promotable |
| --- | --- | --- | --- |
| success | yes | yes | yes |
| blocking_quality_failure | yes (failed check recorded) | no | no |
| nonblocking failure (warning-only) | yes | yes (passed_with_warnings) | yes, if no other block |
| no_data | yes (explicit no-data recorded) | depends | depends on policy, never inferred from emptiness |
| missing_input | yes (missing-input recorded) | no | no |
| cancelled | yes (cancellation recorded) | no | no |
| abandoned | yes (abandonment recorded) | no | no |
| retry | new attempt id; prior attempt evidence retained | per-attempt | per-attempt; a failed prior attempt does not poison a clean retry |

A failed attempt's evidence is retained and never overwritten by a retry. A run
whose recorded evidence is incomplete is never reported healthy, even if the
underlying data write succeeded.

### 4. Evidence-sink outage behavior

A post-write sink failure **does not rewrite the provider result** and must not
automatically rerun the data mutation (the provider write is not replayed).

- The structured emission outcome (persisted / not persisted) is visible to
  orchestration through the emission result and to reconciliation as an
  `unavailable` persistence fact.
- Reconciliation represents unavailable persistence as `unavailable`, never
  `passed`. It must not claim an outage was itself durably persisted.
- Retry after a sink outage uses a **new attempt id**; the same attempt is never
  re-emitted as success.
- WAP/release acceptance fails closed when required evidence is incomplete or
  unavailable.

### 5. Implementation and acceptance boundaries

- Plan 007 owns the neutral composition contract and the root manifest
  (no provider implementation).
- Plan 008 owns provider contributions and the blessed runtime matrix.
- Neither plan promotes a support gate; artifact-bound repeated evidence is
  deferred to Plans 015–016.

## Consequences

- WAP promotion becomes evidence-gated: an incomplete or unavailable profile
  cannot promote, and a completed-but-unpersisted sink outage stays visible.
- Providers gain one declarative contribution seam; core never imports them.
- Sink failures no longer look like success, and duplicate writes are never
  triggered automatically.
- Profile semantic changes require a version/digest bump, never in-place
  reinterpretation.

## Alternatives Considered

- **Inferring evidence from table/catalog existence**: rejected. Existence is
  not an authoritative provider-owned observation and conflates data outcome
  with evidence completeness.
- **Failing the data write when the sink fails**: rejected. It would make an
  observational sink a write-path availability dependency and can cause
  duplicate-write ambiguity on retry.
- **Empty profile meaning healthy**: rejected. A wholly absent provider set must
  read as unavailable/incomplete.
- **Promotion on provider success alone**: rejected. Promotion requires
  complete durable evidence, not only a successful data write.

## Related

- ADR 0047: V1 Production Trust and Readiness Contract (run-evidence contract).
- Plans 007 (composition contract) and 008 (provider contributions/matrix).
- `src/phlo/run_evidence/` (reconciler, emit, hooks) and
  `packages/phlo-dagster/src/phlo_dagster/wap_sensors.py`.
