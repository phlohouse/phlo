# 2. Limit datetime coercion during Pandera validation

Date: 2025-12-14

## Status

Accepted

## Context

Ingestion validation uses Pandera schemas with `coerce=True` defaults.
Naively coercing many columns to datetime can:

- Mask upstream data issues by converting broad inputs unexpectedly.
- Add unnecessary CPU overhead on large datasets.
- Create surprising behavior when non-datetime columns are coerced (or attempted).

We need validation to be strict enough to catch real schema issues while remaining performant and
predictable.

## Decision

When validating with Pandera, only attempt datetime coercion for columns that are:

- Declared as datetime in the Pandera schema, and
- Present in the incoming dataframe, and
- Currently stored as object/string types that Pandas can reasonably parse.

All other columns are left untouched and Pandera validation remains the source of truth.

## Consequences

- Validation is more predictable and less “magical”.
- Performance improves on wide tables where only a small number of datetime columns exist.
- Some dirty-but-coercible values may still pass; this remains a schema design choice (strictness
  should live in the schema, not hidden coercion logic).
