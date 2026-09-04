# ADR 0046: Phlo Contracts for Table-Store-Native Migration Scaffolding

## Status

**Proposed**

## Date

2026-03-01

## Context

Recent capability work established the primitives needed for provider-native schema workflows:

- ADR 0041 introduced capability-first composition.
- Commit `23d361d3` expanded `TableStore` protocols and capability surfaces.
- Commit `c57bd3c7` added schema migration primitives (`NormalizedSchema`, `SchemaMigrationPlan`),
  provider migrators, and `phlo schema-migrate` CLI.
- Commit `59b1ddc2` moved schema conversion from ingestion internals into the active table-store
  provider boundary.

Current gap: migration and scaffold flows still depend on runtime schema discovery and/or manual
schema inputs. We lack a persisted, Phlo-native contract artifact that can drive:

- migration YAML scaffolding,
- schema diff/plan/apply consistency,
- quality + transform context for change review,
- provider-specific execution details without coupling CLI to one store (Iceberg today, others later).

## Decision

Introduce a **Phlo Contract** artifact as the source of truth for schema-driven scaffold and
migration workflows.

### 1. Canonical Contract Model

Define a versioned, JSON-serializable contract model (provider-agnostic core, provider-specific
extensions):

- identity: `flow_id`, `domain`, `table_name`, `asset_key`
- provider binding: `table_store.name`, `schema_migrator.name`, provider metadata
- normalized schema: `NormalizedSchema` projection used by migration core
- provider schema snapshot/fingerprint: optional physical-schema payload/hash for drift detection
- quality payload: validation schema reference + resolved checks/constraints
- transform payload: dbt model/source refs and dependency metadata
- lineage metadata: upstream/downstream asset references
- generation metadata: timestamps, schema extractor version, contract version

### 2. Provider Hooks for Contract IO

Keep capability boundaries explicit:

- `SchemaExtractor` produces normalized schema from quality-native definitions.
- `TableStore`/migrator providers may expose optional contract helpers:
  - normalize physical schema -> `NormalizedSchema`
  - emit provider-specific migration hints
  - validate contract applicability against current table state

Core CLI consumes interfaces, not provider internals.

### 3. Contract Persistence Strategy

Default persistence: file artifact in project state, e.g. `.phlo/contracts/<table>.json`.

Optional backends (future):

- table-store-managed contract table,
- catalog metadata store,
- remote registry service.

CLI behavior remains stable across storage backend choice.

### 4. Scaffold from Contract (No Manual Schema Re-pass)

Add schema-migrate scaffold path that reads the contract and emits migration YAML files:

- `phlo schema-migrate export-contract <table>`
- `phlo schema-migrate scaffold-yaml <table> [--from-contract <path>]`

Generated YAML includes:

- table identity and provider metadata,
- ordered operations (`add`, `drop`, `rename`, `widen_type`, etc.),
- classification (`safe`/`warning`/`breaking`),
- approval requirements,
- quality/transform impact notes,
- deterministic operation IDs for review/audit.

### 5. Single Core for Diff/Plan/Apply/Scaffold

`diff`, `plan`, `apply`, and `scaffold-yaml` use the same contract + migration primitives to avoid
logic drift between preview and generated artifacts.

## Non-Goals

- Replace provider-native migration engines.
- Force one persistence backend for all deployments.
- Encode full runtime DAG definitions in the first contract version.

## Consequences

### Positive

- Eliminates repeated schema-file arguments during scaffold workflows.
- Makes migration scaffolding deterministic and auditable.
- Aligns ingestion, quality, transform, and migration contexts.
- Preserves provider extensibility for non-Iceberg table stores.

### Negative

- Adds contract lifecycle management (versioning, freshness, drift checks).
- Requires clear failure modes when contract and live schema diverge.
- Introduces new CLI and docs surface area.

## Implementation Plan

### Phase 1: Contract Core

- Add `PhloContract` datamodel + serializer.
- Add extractor pipeline from discovered quality schema + migration primitives.
- Add CLI: `schema-migrate export-contract`.

### Phase 2: Scaffold Integration

- Add CLI: `schema-migrate scaffold-yaml`.
- Generate migration YAML from contract + live diff.
- Add regression tests for deterministic output and classification mapping.

### Phase 3: Provider Enrichment

- Add optional provider hooks for physical schema normalization and hints.
- Implement Iceberg enrichment first.
- Validate graceful fallback for providers without enrichment hooks.

### Phase 4: Lifecycle and Observability

- Emit hook events for contract export/scaffold/apply lifecycle.
- Add contract freshness and drift diagnostics in CLI output.
- Document contract update workflow in operations and reference docs.

## Testing Strategy

- Unit: contract serialization, versioning, deterministic IDs.
- Unit: YAML scaffold rendering from fixed contract fixtures.
- Integration: `export-contract -> scaffold-yaml -> plan/apply` round-trip.
- Regression: provider fallback when optional hooks are absent.

## Decisions from Review

- Contract updates should run automatically during `phlo materialize` (with an opt-out flag for
  advanced workflows if needed).
- Default artifact path for multi-environment repos remains open and will be finalized in
  implementation based on practical usage feedback.
- Contract file commit policy should be user-controlled (project choice), not a hard framework
  default.

## Related

- ADR 0041: Capability Primitives and Orchestrator Adapters
- ADR 0044: CLI Command Ownership by Package
- ADR 0045: Package-Owned Settings and CLI Extraction
