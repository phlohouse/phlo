# 13. Generate Pandera schemas from DLT inference via CLI sample runs

Date: 2025-12-14

## Status

Accepted

## Context

Phlo users often start ingestion before a stable schema is known.
DLT can infer schema from sampled source data, but drift can occur if Pandera validation schemas
aren’t kept in sync with the actual extracted columns and types.

We want a first-class, non-Dagster mechanism to:

- Run a small DLT sample (without materializing Dagster assets).
- Extract DLT-inferred column types/nullability.
- Generate Pandera `DataFrameModel` schemas (using `PhloSchema`) into the user’s lakehouse code.

This decision is tracked and implemented by bead `phlo-nwk.3.5` (closed).

## Decision

- Add `phlo schema generate`:
  - Accepts a Python reference to either:
    - a _callable_ returning a DLT source/resource (or iterable of records), or
    - a Dagster `AssetsDefinition` created via `phlo.ingest.dlt(...)`, from which we extract the wrapped
      source-building function.
  - Executes a bounded “sample run” to a local filesystem destination and reads DLT’s inferred
    schema from the resulting pipeline schema.
  - Emits a `PhloSchema` class for the selected DLT table/resource.
- Default behavior is non-destructive:
  - Prints generated code (dry-run).
  - Refuses to overwrite existing schema files unless explicitly requested.

## Consequences

- Users can bootstrap schemas early, without needing Iceberg tables to exist.
- Schema generation becomes reproducible and can be re-run to prevent drift.
- This introduces a small amount of DLT coupling in the CLI surface area that must be maintained.
