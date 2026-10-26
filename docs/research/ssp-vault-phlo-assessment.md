# Core-package improvements informed by the SSP vault

## Implemented changes

The first three proposals below now have local core and provider implementations.
The original source observations describe the unchanged baseline, not current
limitations. The fourth proposal remains deferred pending a concrete need.

- Explicit freshness adds a timestamp column and optional historical reference
  to `SLA`. Pandera executes the promise through the existing quality path.
  [Usage and runnable demonstration](../../examples/lakehouses/iot-telemetry/README.md#check-event-time-freshness-locally).
- Data intervals add UTC half-open runtime bounds, Dagster calendar-window
  translation, and opt-in stateless bounded DLT extraction with merge writes.
  [Usage and guarantees](../guides/data-intervals.md).
- Neutral `grain` and `reconcile_aggregate` rules execute SQL checks through
  Pandera and feed existing quality evidence and Dataset publication policy.
  [Usage and real-lakehouse demonstration](../../examples/lakehouses/wap-failure-lab/README.md#dataset-constraint-demonstration).

Verification includes identical before-and-after demonstrations and real local
services. Reconciliation catches a grouped join that inflates revenue from 41 to
52 despite unique summary keys. Dataset blocker policy then refuses publication,
where the baseline publishes. Freshness rejects 30-hour-old events against a
two-hour promise, while ingestion-time freshness still passes. Real Trino,
Iceberg, Nessie, and PostgreSQL checks also reject empty and over-limit data,
accept the exact freshness boundary, and retain the expected Dataset state.

The interval example runs through public provider discovery, Dagster, DLT, and
Nessie with MinIO. It retains a boundary record, recovers a late arrival through
lookback, and replays without duplicate business IDs. Separate real DLT and
local Iceberg checks preserve a persisted live cursor across replay, rejected
writes, committed-but-reply-lost failures, retry, and empty extraction.

These are development checks, not release qualification or deployment evidence.
Dataset policy tests do not prove physical Nessie promotion. Interval mode does
not create an external-cursor/table transaction, infer source query pushdown,
or change legacy unbounded DLT progress semantics. See each linked guide for
the exact guarantees and limits.

## Recommendation

Improve the contracts that workflow authors use in Flow Core and the guarantees
that provider packages implement. Examples demonstrate those changes afterwards.
An example-only recovery test is useful verification, but is not itself the
framework improvement requested here.

The initial recommendation was explicit freshness semantics shared by contract
declarations, quality execution, and evidence, followed by data intervals and
dataset constraints. The sections below preserve the reasoning and baseline
source observations behind those changes.

The [source notes](ssp-vault-source-notes.md) contain the external research.
This assessment samples relevant topics, not the vault's entire advertised
1,000-plus notes. The implementation summary above records the resulting work.

## 1. Explicit freshness semantics across the core and providers

The vault's contracts and software-defined asset topics suggest that useful
data needs a measurable consumer promise, rather than just successful execution.

Current evidence:

- [SLA](../../src/phlo/contracts.py) records a freshness threshold, but not the
  timestamp or clock against which to evaluate it.
- [Flow declarations](../../src/phlo/flow.py) expose freshness in contract,
  publish, and observe metadata. Those declarations have different roles.
- [Neutral quality rules](../../src/phlo/quality_rules.py) already support
  timestamp-column freshness. The [SLA helper](../../src/phlo/helpers/quality.py)
  defaults to `_phlo_ingested_at`.
- The [Dagster adapter](../../packages/phlo-dagster/src/phlo_dagster/adapter.py)
  separately translates `RunSpec.freshness_hours` to a Dagster freshness policy.

Proposed package change: give the consumer freshness contract an explicit
timestamp basis and evaluation reference. Compile it into the existing neutral
quality rules, preserve it in governance projections, and record the evaluated
timestamp and threshold in existing run evidence. Keep materialisation freshness
distinct from source-data freshness. Do not silently reinterpret existing values.

Core owns the meaning and validation. Pandera and orchestrator adapters own
execution. Existing provider-specific checks remain valid escape routes.
Completeness remains a separate assertion, because a maximum timestamp cannot
prove that every expected record arrived.

Acceptance: reloading old source events does not satisfy an event-time promise.
A historical replay evaluates against its declared reference, not an implicit
wall clock. Missing or unsupported timestamp semantics are explicit, not a pass.
An example then demonstrates the same declaration through the supported stack.

## 2. Explicit data intervals and incremental progress semantics

The vault's functional engineering, idempotency, and incremental-loading notes
support bounded, repeatable units of work.

Current evidence:

- [PartitionSpec](../../src/phlo/capabilities/specs.py) carries kind, start date,
  and timezone. [RuntimeContext](../../src/phlo/capabilities/runtime.py) exposes
  a partition key, but no standard start/end data interval.
- [Incremental helpers](../../src/phlo/helpers/incremental.py) provide watermarks,
  lookback windows, predicates, and dictionary state updates. These helpers do
  not themselves provide a durable commit protocol.
- [Stream checkpoints](../../src/phlo/capabilities/interfaces.py) already bind
  source offset ranges to snapshots and publication. They are a precedent to
  assess, not a reason to add a competing checkpoint system.

Proposed package change: carry a validated half-open data interval through
discovery and runtime execution. Give scheduled loads and historical replays
explicit progress semantics. Map these to dlt cursors and existing checkpoint
and publication contracts where compatible. Do not promise a distributed atomic
transaction between an external source cursor and a table commit.

Core owns interval and replay semantics. Dagster translates scheduling windows,
ingestion providers translate extraction bounds, and durable state remains with
the appropriate provider. Keep orchestration partitions separate from physical
Iceberg partition transforms.

Acceptance: adjacent windows neither lose nor duplicate boundary records under
the declared write mode. A historical replay does not advance live progress.
An ambiguous write cannot advance progress merely because extraction finished.
Timezone and daylight-saving boundaries need explicit tests.

## 3. Dataset grain and reconciliation in neutral contracts

The vault's modelling and contracts topics suggest declaring what a row means
and checking that transformations preserve the intended business quantities.

Current evidence:

- [QualityRule factories](../../src/phlo/quality_rules.py) cover nullability,
  uniqueness, freshness, ranges, and accepted values.
- The [Pandera provider](../../packages/phlo-pandera/src/phlo_pandera/plugin.py)
  already translates those neutral rules and exposes richer reconciliation
  checks. [Core helpers](../../src/phlo/helpers/reconciliation.py) also compare
  counts, aggregates, keys, and checksums.
- [ContractSpec](../../src/phlo/flow.py) carries governance metadata but has no
  explicit grain or reconciliation declaration.

Proposed package change: extend the existing contract and rule path with a
small set of dataset constraints, initially composite grain and aggregate
reconciliation. Reuse the current quality-provider translation interface.
Declare comparison relations and scopes explicitly so discovery can validate
references and execution can target the intended candidate data.

Core owns the declarations and observable result meaning. Quality providers
compile and execute them using existing query mechanisms. Feed their results
into existing quality evidence and publication policy, not a new policy engine.
Preserve advisory versus blocking severity. Unsupported rules must be explicit.

Acceptance: a join that multiplies revenue fails reconciliation even if its SQL
and column types are valid. Composite grain detects repeated business identities.
Checks have explicit partition or full-table scope and do not require loading
the whole dataset into Python by default.

## 4. Negotiate write guarantees before execution

The vault's idempotency guidance is useful only if the framework distinguishes
which write modes can actually satisfy the requested behaviour.

Current evidence:

- [TableStoreSupport](../../src/phlo/capabilities/interfaces.py) already
  advertises schema policies, history, and other optional operations.
- [Schema-policy negotiation](../../src/phlo/capabilities/table_store.py) rejects
  unsupported explicit policies before writes.
- [HistoryPolicy](../../src/phlo/capabilities/history.py) specifies immutable
  version identity and comparison fields. It already distinguishes conflicts
  from unknown commit outcomes.

Proposed package change: extend that existing preflight path only where a
concrete workflow needs a guarantee not represented today. Validate the
combination of extraction mode, identity, write mode, replay intent, and provider
support. Report what is guaranteed and what remains source-dependent.
Do not label all merge operations exactly-once or treat changed snapshot IDs as
proof of duplicate business data.

This is lower priority than the first three proposals. A concrete unsupported
combination must be identified before introducing additional support flags or
public types. If current negotiation already rejects it correctly, no new
abstraction is needed.

## Implementation order and limits

Start with explicit freshness semantics because existing SLA and quality-rule
interfaces provide a bounded path to a user-visible improvement. Follow with
data intervals if consistent incremental execution is the higher product need,
or neutral dataset constraints if modelling correctness is more urgent.

For each change, assess plugin consumers before changing public contracts,
implement the smallest compatible core/provider slice, test its public behaviour,
and then demonstrate it in an existing example. Examples are acceptance evidence,
not the primary deliverable.

Do not rebuild WAP, the schema registry, durable reports, or Dataset publication
policy. Do not adopt permanent retention, universal partition replacement,
another orchestrator, or a custom semantic engine merely because the vault
describes them. Keep source-specific extraction and engine-specific writes in
providers rather than moving them into Flow Core.
