# 5. Store dbt compiled SQL in Dagster asset metadata (not descriptions)

Date: 2025-12-14

## Status

Accepted

## Context

Dagster asset descriptions are intended to be human-facing summaries, but compiled SQL can be large.
Embedding compiled SQL in descriptions:

- Bloats UI payloads and makes descriptions noisy.
- Increases risk of exceeding size limits or reducing readability.
- Makes it harder for consumers to selectively fetch/use the SQL.

We still want compiled SQL available for tooling (lineage, debugging, “open in SQL” workflows).

## Decision

Use a custom `DagsterDbtTranslator` to:

- Improve group naming heuristics for dbt models.
- Attach compiled SQL to asset **metadata** (e.g., `phlo/compiled_sql` and related fields), with
  truncation controls and source tracking.

## Consequences

- UIs and consumers can access compiled SQL when needed without polluting descriptions.
- Metadata size is still bounded; truncation is explicit and discoverable.
- Translator becomes the central place to evolve dbt→Dagster mapping semantics.
