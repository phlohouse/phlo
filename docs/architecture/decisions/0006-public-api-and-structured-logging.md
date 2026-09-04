# 6. Maintain explicit public exports and avoid print() in library code

Date: 2025-12-14

## Status

Accepted

## Context

Phlo is a library + CLI. For good developer ergonomics and type checking:

- The public API should be explicit (`__all__`) and stable.
- Import-time side effects should be minimized (lazy imports where appropriate).

Separately, `print()` in library modules creates noisy output and is hard to route/structure compared
to standard logging.

## Decision

- Use explicit `__all__` exports for public modules and resources, with typed/lazy imports where
  necessary.
- Replace non-CLI `print()` usage with structured logging (`phlo.logging.get_logger(__name__)`
  and Dagster context logs where applicable).

## Consequences

- basedpyright has a clearer view of the public surface area.
- Users get consistent logs that can be routed/filtered across environments.
- Some internal refactors are required to avoid import cycles while keeping exports explicit.
