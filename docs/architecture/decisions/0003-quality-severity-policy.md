# 3. Define a severity policy for quality checks (blocking vs warn)

Date: 2025-12-14

## Status

Accepted

## Context

Not all quality failures should have the same impact:

- Contract/schema violations are typically correctness failures and should block downstream work.
- Some tests (e.g., anomaly/drift) are informative and should be warn-only.

We need a uniform policy so check producers and consumers agree on semantics, and so automation can
rely on consistent status meaning.

## Decision

Adopt a shared severity mapping:

- Pandera contract failures are **ERROR** (blocking).
- dbt tests are **ERROR** for `not_null`, `unique`, and `relationships`; other test types default to
  **WARN**.
- dbt tags can override:
  - `tag:blocking` forces **ERROR**
  - `tag:warn` or `tag:anomaly` forces **WARN**

## Consequences

- Users get sensible defaults without needing to annotate every test.
- Teams can still override per-test behavior via tags.
- Consumers (Observatory, sensors) can treat WARN and ERROR differently without custom heuristics.
