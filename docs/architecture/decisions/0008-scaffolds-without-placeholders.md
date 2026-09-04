# 8. Scaffold generators must emit working code (no TODO placeholders by default)

Date: 2025-12-14

## Status

Accepted

## Context

Early development can tolerate breaking changes, but generated scaffolds should still be immediately
actionable. Emitting TODO-heavy templates:

- Produces “slop” that users must manually clean up.
- Leads to inconsistent patterns across projects.
- Makes it harder to scale beyond a single developer.

## Decision

All `phlo` scaffolding commands should:

- Require explicit inputs for values that cannot be reasonably inferred.
- Generate minimal working code/config without TODO placeholders.
- Provide flags (e.g., `--field name:type`) to ensure generated artifacts are valid and typed.

## Consequences

- Scaffolds are more reliable and reduce follow-up manual work.
- Users may need to supply more inputs up front (a deliberate tradeoff).
- Template logic must remain strict to prevent regressions into placeholder output.
