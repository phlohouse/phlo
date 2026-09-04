# 9. Scaffold `publishing.yaml` from dbt manifest with an idempotent merge strategy

Date: 2025-12-14

## Status

Accepted

## Context

Publishing configuration should reflect project reality (dbt models, dependencies, and metadata),
but it is also hand-edited.
We need a CLI that can:

- Bootstrap a valid `publishing.yaml`.
- Update it as the project evolves without clobbering manual edits.
- Restrict scope (e.g., only marts models) and support dry runs.

## Decision

Implement `phlo publishing scaffold` that:

- Reads dbt `manifest.json` directly for accurate model metadata.
- Generates/updates `publishing.yaml` using an idempotent merge strategy that preserves existing
  fields and only adds missing stanzas/values.
- Supports `--select`, `--dry-run`, and output path controls.

## Consequences

- Publishing config stays in sync with dbt with low manual effort.
- The scaffold must be careful about merge semantics; “preserve edits” becomes a contract.
- The CLI depends on dbt artifacts being present (manifest generation becomes part of workflow).
